"""From a finding to work: tasks, supplier reminders, purchasing holds and signed webhooks.

Every status change writes an event to the outbox in the same transaction as the change (engine.py). The dispatcher
turns each event into actions, in order, and marks it dispatched, all in one transaction: if anything fails or the
process dies, the event is handled again from scratch, and the database keeps each action single (one open task per
requirement, one open hold per engagement, unique keys on reminders and webhook messages). Outside effects happen
after the transaction: webhook messages are sent by `deliver`, at least once, with a stable id the receiver uses to
ignore repeats, so the effect happens once.

The policy (the pack's `actions` section):
- **Chase the supplier** for insurance that is missing, expired, not satisfied or due for renewal: a task with a due
  date set by the engagement's risk tier, and a reminder to the supplier, at most one per cooldown.
- **Follow up**: any other unmet requirement becomes a task for the buyer's staff.
- **Hold**: while insurance or bank details are expired or not satisfied, the engagement is on a purchasing hold,
  released by itself when they are put right.
- A task closes by itself when its requirement is met again.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
from collections import Counter
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .pack import Pack
from .store import (
    Delivery,
    Engagement,
    Hold,
    Notification,
    OutboxEvent,
    RequirementState,
    Store,
    Supplier,
    Task,
    WebhookEndpoint,
)

MAX_ATTEMPTS = 8


# ---------------------------------------------------------------------------------------------- dispatching events

def dispatch(store: Store, pack: Pack, tenant_id: int, now: datetime, fail_after: int | None = None) -> Counter:
    """Turn the tenant's undispatched events into actions, oldest first, one transaction per event. `fail_after=k`
    raises inside the k-th event's transaction (after its actions were written): a crash test."""
    with store.tenant(tenant_id) as s:
        ids = list(s.scalars(select(OutboxEvent.id).where(OutboxEvent.dispatched_at.is_(None))
                             .order_by(OutboxEvent.id)))
    done: Counter = Counter()
    for n, event_id in enumerate(ids, 1):
        with store.tenant(tenant_id) as s:
            ev = s.get(OutboxEvent, event_id)
            if ev is None or ev.dispatched_at is not None:
                continue                                   # another dispatcher got there first
            if _superseded(s, ev):
                counts = Counter({"superseded": 1})        # act on the latest state, not on every step to it
            else:
                counts = _act(s, pack, ev, now)
            ev.dispatched_at = now
            s.flush()
            if fail_after is not None and n == fail_after:
                raise RuntimeError("dispatcher died mid-event")
        done.update(counts)
        done["events"] += 1
    return done


def _superseded(s: Session, ev: OutboxEvent) -> bool:
    """A newer change of the same requirement is already waiting. During a bulk load a requirement passes through
    several statuses in seconds (missing, then satisfied); acting on each would open and close work for nothing."""
    if "#" not in ev.key:
        return False
    prefix = ev.key.split("#", 1)[0] + "#"
    return s.scalar(select(OutboxEvent.id).where(OutboxEvent.key.like(prefix + "%"), OutboxEvent.id > ev.id,
                                                 OutboxEvent.dispatched_at.is_(None)).limit(1)) is not None


def _act(s: Session, pack: Pack, ev: OutboxEvent, now: datetime) -> Counter:
    policy, p, out = pack.actions, ev.payload, Counter()
    eid, rid, to = p["engagement"], p["requirement"], p["to"]
    e = s.get(Engagement, eid)
    supplier = s.get(Supplier, e.supplier_id)
    chase = rid in policy.chase_requirements and (to in policy.chase_statuses
                                                  or bool(set(p["failing"]) & set(policy.chase_findings)))
    follow_up = not chase and rid not in policy.chase_requirements and to in policy.follow_up_statuses
    task = s.scalar(select(Task).where(Task.engagement_id == eid, Task.requirement_id == rid, Task.status == "open"))
    hints = " ".join(p["hints"]) or f"The requirement is {to.lower().replace('_', ' ')}."
    if chase or follow_up:
        kind = "chase_supplier" if chase else "follow_up"
        if task is not None and task.kind != kind:
            task.status, task.closed_at, task.closed_reason = "superseded", now, f"now {kind}"
            s.flush()
            task = None
        if task is None:
            title = (f"Chase {supplier.legal_name} for {rid.replace('_', ' ')} evidence" if chase else
                     f"Follow up {rid.replace('_', ' ')} for {supplier.legal_name} ({to.lower().replace('_', ' ')})")
            s.add(Task(engagement_id=eid, requirement_id=rid, kind=kind, title=title, detail=hints, opened_at=now,
                       due_at=now + timedelta(days=policy.task_due_days.get(e.tier, 15)), event_key=ev.key))
            out["task_opened"] += 1
        else:
            task.detail = hints
            out["task_updated"] += 1
        if chase:
            out[_remind(s, policy, ev, supplier, eid, rid, hints, now)] += 1
    elif task is not None:
        task.status, task.closed_at, task.closed_reason = "done", now, f"requirement now {to.lower()}"
        out["task_closed"] += 1
    out.update(_hold(s, pack, ev, eid, now))
    _announce(s, ev, "requirement.status_changed", p, now)
    return out


def _remind(s: Session, policy, ev: OutboxEvent, supplier: Supplier, eid: int, rid: str, hints: str,
            now: datetime) -> str:
    last = s.scalar(select(Notification).where(Notification.engagement_id == eid, Notification.requirement_id == rid)
                    .order_by(Notification.created_at.desc()))
    if last is not None and now - last.created_at < timedelta(days=policy.notify_cooldown_days):
        return "reminder_suppressed"
    s.add(Notification(key=f"{ev.key}:reminder", supplier_id=supplier.id, engagement_id=eid, requirement_id=rid,
                       subject=f"Action needed: {rid.replace('_', ' ')} evidence for {supplier.legal_name}",
                       body=f"Dear {supplier.legal_name},\n\n{hints}\n\nPlease upload the evidence through the "
                            "supplier portal.", created_at=now))
    return "supplier_reminded"


def _hold(s: Session, pack: Pack, ev: OutboxEvent, eid: int, now: datetime) -> Counter:
    """A hold mirrors the engagement's current statuses, whatever order events arrive in."""
    policy, out = pack.actions, Counter()
    holding = sorted(st.requirement_id for st in s.scalars(select(RequirementState).where(
        RequirementState.engagement_id == eid)) if st.requirement_id in policy.hold_requirements
        and st.status in policy.hold_statuses)
    hold = s.scalar(select(Hold).where(Hold.engagement_id == eid, Hold.released_at.is_(None)))
    if holding and hold is None:
        s.add(Hold(engagement_id=eid, requirements=holding, placed_at=now))
        _announce(s, ev, "hold.placed", {"engagement": eid, "requirements": holding}, now)
        out["hold_placed"] += 1
    elif hold is not None and not holding:
        hold.released_at = now
        _announce(s, ev, "hold.released", {"engagement": eid}, now)
        out["hold_released"] += 1
    elif hold is not None and hold.requirements != holding:
        hold.requirements = holding
    return out


def _announce(s: Session, ev: OutboxEvent, event_type: str, data: dict, now: datetime) -> None:
    """One message per active endpoint; its id is fixed by the event and type, so a retried dispatch adds nothing."""
    for ep in s.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))):
        wid = f"msg_{hashlib.sha256(f'{ev.tenant_id}|{ev.key}|{event_type}'.encode()).hexdigest()[:24]}"
        if s.scalar(select(Delivery.id).where(Delivery.endpoint_id == ep.id, Delivery.webhook_id == wid)) is None:
            s.add(Delivery(endpoint_id=ep.id, webhook_id=wid, event_type=event_type,
                           payload={"type": event_type, "data": data, "event": ev.key}, next_attempt_at=now))


# ---------------------------------------------------------------------------------------------- signed webhooks

def new_secret(seed: bytes | None = None) -> str:
    raw = hashlib.sha256(seed).digest() if seed is not None else random.SystemRandom().randbytes(32)
    return "whsec_" + base64.b64encode(raw).decode()


def sign(secret: str, msg_id: str, timestamp: int, body: str) -> dict[str, str]:
    """Standard Webhooks headers: an HMAC-SHA256 over id, timestamp and body, with the endpoint's secret."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.{body}".encode(), hashlib.sha256).digest()
    return {"webhook-id": msg_id, "webhook-timestamp": str(timestamp),
            "webhook-signature": "v1," + base64.b64encode(mac).decode()}


def verify(secret: str, headers: dict[str, str], body: str, now_ts: int, tolerance: int = 300) -> bool:
    """True if the signature is the endpoint's and the message is fresh (a replayed old message is refused)."""
    try:
        ts = int(headers["webhook-timestamp"])
    except (KeyError, ValueError):
        return False
    if abs(now_ts - ts) > tolerance:
        return False
    expected = sign(secret, headers.get("webhook-id", ""), ts, body)["webhook-signature"].split(",", 1)[1]
    return any(hmac.compare_digest(expected, part.split(",", 1)[1])
               for part in headers.get("webhook-signature", "").split() if part.startswith("v1,"))


class Transport(Protocol):
    def post(self, url: str, body: str, headers: dict[str, str]) -> int: ...


class Receiver:
    """The tenant's system at the other end, for tests and simulations: checks the signature, processes a message
    once, and can be told to fail. `ack_loss` processes the message and then fails to answer, so the sender retries
    a message that was in fact received: the duplicate is recognised by its id and ignored."""

    def __init__(self, secret: str, clock, outage: float = 0.0, ack_loss: float = 0.0, seed: int = 0):
        self.secret, self.clock = secret, clock
        self.outage, self.ack_loss, self.rng = outage, ack_loss, random.Random(seed)
        self.processed: list[dict] = []
        self.seen: set[str] = set()
        self.duplicates = self.rejected = self.refused = 0

    def post(self, url: str, body: str, headers: dict[str, str]) -> int:
        if self.rng.random() < self.outage:
            self.refused += 1
            return 503
        if not verify(self.secret, headers, body, int(self.clock().timestamp())):
            self.rejected += 1
            return 401
        wid = headers["webhook-id"]
        if wid in self.seen:
            self.duplicates += 1
            return 200
        self.seen.add(wid)
        self.processed.append(json.loads(body))
        return 500 if self.rng.random() < self.ack_loss else 200


def deliver(store: Store, tenant_id: int, transport: Transport, now: datetime) -> Counter:
    """Send the tenant's due webhook messages. Sending happens outside any transaction; each attempt's outcome is
    recorded after. Backoff doubles from 30 seconds; after MAX_ATTEMPTS a message is dead, for a person to see."""
    with store.tenant(tenant_id) as s:
        due = [(d.id, d.webhook_id, d.payload, d.attempts, ep.url, ep.secret)
               for d, ep in s.execute(select(Delivery, WebhookEndpoint).join(
                   WebhookEndpoint, WebhookEndpoint.id == Delivery.endpoint_id).where(
                   Delivery.status == "pending", Delivery.next_attempt_at <= now).order_by(Delivery.id)).all()]
    out = Counter()
    for did, wid, payload, attempts, url, secret in due:
        body = json.dumps(payload, sort_keys=True)
        try:
            code, error = transport.post(url, body, sign(secret, wid, int(now.timestamp()), body)), ""
        except Exception as exc:  # noqa: BLE001 - a network failure is an outcome to record and retry
            code, error = 0, f"{type(exc).__name__}: {exc}"
        with store.tenant(tenant_id) as s:
            d = s.get(Delivery, did)
            d.attempts = attempts + 1
            if 200 <= code < 300:
                d.status, d.delivered_at = "delivered", now
                out["delivered"] += 1
            elif d.attempts >= MAX_ATTEMPTS:
                d.status, d.last_error = "dead", error or f"HTTP {code}"
                out["dead"] += 1
            else:
                d.next_attempt_at, d.last_error = now + timedelta(seconds=30 * 2 ** (d.attempts - 1)), error or f"HTTP {code}"
                out["retry_scheduled"] += 1
    return out


def send_reminders(store: Store, tenant_id: int, now: datetime) -> int:
    """Mark written reminders as sent (the e-mail gateway is outside this repository)."""
    with store.tenant(tenant_id) as s:
        pending = s.scalars(select(Notification).where(Notification.sent_at.is_(None))).all()
        for n in pending:
            n.sent_at = now
        return len(pending)
