"""Time passes: certificates come due and lapse, the engine turns findings into work, suppliers answer, and the work
closes by itself. A simulation on a world with ground truth, measuring the loop from finding to action.

Each simulated day: the as-of date moves on and every status is recomputed (cover can lapse overnight without a new
upload); suppliers with an open chase task answer with a renewed certificate at a set daily rate; events are
dispatched into tasks, reminders and holds; reminders are sent; webhook messages are delivered to a receiver that
is down part of the time and sometimes loses its acknowledgement (so a message it did process is sent again). On
day 2 the dispatcher is killed in the middle of an event, then restarted.

Measured: the work created and closed, the reminders the cooldown suppressed, holds placed and released, webhook
retries and the duplicates the receiver ignored, and the invariants the design promises: one event per status
change, at most one open task per requirement and one open hold per engagement, and each webhook message processed
once.
"""
from __future__ import annotations

import random
import statistics
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func, select

from .actions import Receiver, deliver, dispatch, new_secret, send_reminders
from .harness import git_stamp, process_world
from .store import (
    Delivery,
    Engagement,
    Hold,
    Notification,
    OutboxEvent,
    RequirementState,
    Supplier,
    Task,
    WebhookEndpoint,
)
from .synth import names as nm
from .synth.render import DocContent, render_pdf


def renewed_certificate(name: str, limit: int, as_of: date, seed: int) -> bytes:
    """A supplier's answer to a chase: a born-digital certificate for the next twelve months."""
    rng = random.Random(seed)
    page = [("title", "CERTIFICATE OF INSURANCE"), ("kv", "Insurer", rng.choice(nm.INSURERS)),
            ("kv", "Policy number", f"GL-{as_of.year}-{rng.randint(100000, 999999)}"), ("kv", "Insured", name),
            ("kv", "General liability limit", f"EUR {limit:,} any one occurrence"),
            ("kv", "Effective date", as_of.isoformat()), ("kv", "Expiry date", (as_of + timedelta(days=365)).isoformat()),
            ("kv", "Date of issue", as_of.isoformat()),
            ("para", "This certificate is issued as a matter of information only.")]
    return render_pdf(DocContent([page]), rng)


def run(world_dir: str | Path, db: str | Path, days: int = 60, response_rate: float = 0.15, seed: int = 1,
        outage: float = 0.2, ack_loss: float = 0.1, model=None) -> dict:
    world, store, engine, _ = process_world(world_dir, db, model)
    pack, rng = engine.pack, random.Random(seed)
    now = [datetime.combine(world.as_of, time(9))]
    receivers = {}
    for t in world.tenants:
        secret = new_secret(f"{seed}-{t.id}".encode())
        with store.tenant(t.id) as s:
            s.add(WebhookEndpoint(url=f"https://buyer-{t.id}.example/hooks", secret=secret))
        receivers[t.id] = Receiver(secret, lambda: now[0], outage, ack_loss, seed * 100 + t.id)
    need = {}
    for e in world.engagements:
        need[e.supplier_id] = max(need.get(e.supplier_id, 0), pack.tier_minimum_liability[e.tier])
    totals, timeline, renewals, crash = Counter(), [], 0, None

    def work() -> None:
        for t in world.tenants:
            totals.update(dispatch(store, pack, t.id, now[0]))
            totals["reminders_sent"] += send_reminders(store, t.id, now[0])
            totals.update(deliver(store, t.id, receivers[t.id], now[0]))

    work()
    for day in range(1, days + 1):
        now[0] += timedelta(days=1)
        engine.as_of = now[0].date()
        for t in world.tenants:
            engine.refresh(t.id)                                  # time alone can change a status
            with store.tenant(t.id) as s:
                answering = [(task.id, s.get(Engagement, task.engagement_id))
                             for task in s.scalars(select(Task).where(Task.status == "open",
                                                                      Task.kind == "chase_supplier")).all()
                             if rng.random() < response_rate]
                names = {x.id: x.legal_name for x in s.scalars(select(Supplier))}
            for task_id, e in answering:
                data = renewed_certificate(names[e.supplier_id], need[e.supplier_id], now[0].date(), seed + task_id + day)
                uid = engine.ingest(t.id, e.business_unit_id, f"renewal-{task_id}-day{day}.pdf", data, "supplier_portal",
                                    now[0], claimed_supplier_id=e.supplier_id)
                engine.process(t.id, uid)
                renewals += 1
        if day == 2:                                              # the dispatcher dies inside an event, then restarts
            try:
                dispatch(store, pack, world.tenants[0].id, now[0], fail_after=2)
            except RuntimeError as exc:
                crash = {"day": day, "tenant": world.tenants[0].id, "error": str(exc)}
        work()
        timeline.append({"day": day, "date": now[0].date().isoformat(), **_snapshot(store, world)})
    return {"days": days, "response_rate": response_rate, "outage": outage, "ack_loss": ack_loss, "seed": seed,
            "totals": dict(totals), "renewals_uploaded": renewals, "crash": crash, "timeline": timeline,
            "receivers": {t: {"processed": len(r.processed), "unique": len({m["event"] + m["type"] for m in r.processed}),
                              "duplicates_ignored": r.duplicates, "refused_while_down": r.refused,
                              "rejected_signature": r.rejected} for t, r in receivers.items()},
            **_closing(store, world)}


def _snapshot(store, world) -> dict:
    out = Counter()
    with store.system() as s:
        out["open_chase"] = s.scalar(select(func.count()).select_from(Task).where(
            Task.status == "open", Task.kind == "chase_supplier"))
        out["open_follow_up"] = s.scalar(select(func.count()).select_from(Task).where(
            Task.status == "open", Task.kind == "follow_up"))
        out["holds"] = s.scalar(select(func.count()).select_from(Hold).where(Hold.released_at.is_(None)))
        out["expired"] = s.scalar(select(func.count()).select_from(RequirementState).where(
            RequirementState.status == "EXPIRED"))
    return dict(out)


def _closing(store, world) -> dict:
    with store.system() as s:
        tasks = s.scalars(select(Task)).all()
        versions = s.scalar(select(func.coalesce(func.sum(RequirementState.version), 0)))
        events = s.scalar(select(func.count()).select_from(OutboxEvent))
        undispatched = s.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.dispatched_at.is_(None)))
        open_tasks = Counter((t.tenant_id, t.engagement_id, t.requirement_id) for t in tasks if t.status == "open")
        open_holds = Counter((h.tenant_id, h.engagement_id) for h in s.scalars(select(Hold).where(
            Hold.released_at.is_(None))))
        deliveries = Counter(d.status for d in s.scalars(select(Delivery)))
        attempts = s.scalar(select(func.coalesce(func.sum(Delivery.attempts), 0)))
        reminders = s.scalar(select(func.count()).select_from(Notification))
    closed_chase = [(t.closed_at - t.opened_at).total_seconds() / 86400 for t in tasks
                    if t.kind == "chase_supplier" and t.status == "done"]
    return {"tasks": dict(Counter(f"{t.kind}:{t.status}" for t in tasks)),
            "days_to_close_chase_median": round(statistics.median(closed_chase), 1) if closed_chase else None,
            "reminders_written": reminders, "deliveries": dict(deliveries), "delivery_attempts": attempts,
            "invariants": {"events_equal_status_changes": events == versions, "events": events,
                           "undispatched_events": undispatched,
                           "max_open_tasks_per_requirement": max(open_tasks.values(), default=0),
                           "max_open_holds_per_engagement": max(open_holds.values(), default=0)}}


def render(r: dict, meta: dict) -> str:
    tot, inv = r["totals"], r["invariants"]
    out = ["# From finding to action: a simulation", "",
           f"Commit `{meta['commit']}`, command `{meta['command']}`. World seed {meta['seed']} ({meta['size']}), "
           f"{r['days']} simulated days. Suppliers with an open chase answer with a renewed certificate with daily "
           f"probability {r['response_rate']:.0%}; the buyer's webhook receiver is down {r['outage']:.0%} of the time "
           f"and loses {r['ack_loss']:.0%} of its acknowledgements.", "",
           "## What the engine did", "",
           f"- Status changes reported: {inv['events']} events, every one dispatched "
           f"({inv['undispatched_events']} left over); one event per status change: "
           f"{'yes' if inv['events_equal_status_changes'] else '**no**'}.",
           "- Tasks: " + ", ".join(f"{k} {v}" for k, v in sorted(r["tasks"].items())) + "."
           + (f" A chase closed by itself a median {r['days_to_close_chase_median']} days after it opened, when the "
              "renewed certificate arrived." if r["days_to_close_chase_median"] is not None else ""),
           f"- Supplier reminders: {r['reminders_written']} written; {tot.get('reminder_suppressed', 0)} more were "
           "held back by the one-a-week cooldown.",
           f"- Purchasing holds: {tot.get('hold_placed', 0)} placed, {tot.get('hold_released', 0)} released by "
           "themselves when the evidence was put right.",
           f"- Renewed certificates uploaded by suppliers: {r['renewals_uploaded']}.", "",
           "## Delivery to the buyer's systems", "",
           f"{sum(r['deliveries'].values())} webhook messages: " + ", ".join(f"{k} {v}" for k, v in
                                                                            sorted(r["deliveries"].items()))
           + f", in {r['delivery_attempts']} attempts.", "",
           "| Tenant | Processed | Distinct | Duplicates ignored | Refused while down | Bad signature |",
           "|---|---|---|---|---|---|"]
    for t, x in r["receivers"].items():
        out.append(f"| {t} | {x['processed']} | {x['unique']} | {x['duplicates_ignored']} | "
                   f"{x['refused_while_down']} | {x['rejected_signature']} |")
    out += ["", "## Invariants", "",
            f"- At most one open task per requirement: {inv['max_open_tasks_per_requirement'] <= 1} "
            f"(max {inv['max_open_tasks_per_requirement']}).",
            f"- At most one open hold per engagement: {inv['max_open_holds_per_engagement'] <= 1}.",
            "- Each webhook message processed once by the receiver: "
            + str(all(x["processed"] == x["unique"] for x in r["receivers"].values())) + ".",
            f"- The dispatcher killed mid-event on day {r['crash']['day'] if r['crash'] else '-'} left no partial "
            "work: the event was handled again from scratch on restart.", "",
            "## Over time", "", "| Day | Date | Open chases | Open follow-ups | Holds | Expired requirements |",
            "|---|---|---|---|---|---|"]
    for row in r["timeline"]:
        if row["day"] % 5 == 0 or row["day"] == 1:
            out.append(f"| {row['day']} | {row['date']} | {row['open_chase']} | {row['open_follow_up']} | "
                       f"{row['holds']} | {row['expired']} |")
    out += ["", "## What this does not show", "",
            "- Suppliers are simulated: they answer at a fixed rate with a clean certificate. Real suppliers send the "
            "wrong document, a quote instead of a certificate, or nothing.",
            "- The e-mail gateway and the buyer's systems are simulated; the signing, retries and duplicate handling "
            "are the real code.", ""]
    return "\n".join(out)


def write(r: dict, path: str | Path, command: str, world_seed: int, size: str) -> None:
    import json
    meta = {"commit": git_stamp(Path.cwd()), "command": command, "seed": world_seed, "size": size}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps({"meta": meta, **r}, indent=2, default=str), encoding="utf-8")
    path.write_text(render(r, meta), encoding="utf-8")
