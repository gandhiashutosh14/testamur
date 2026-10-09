"""From finding to action: one outbox event per status change; dispatching turns events into single tasks, reminders
and holds, even across a crash; putting the evidence right undoes them; webhooks are signed, retried and processed
once."""
from collections import Counter
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from testamur.actions import Receiver, deliver, dispatch, new_secret, sign, verify
from testamur.actionsim import renewed_certificate
from testamur.model import Status
from testamur.pack import load_pack
from testamur.store import (
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


def _now(world):
    return datetime.combine(world.as_of, time(9))


def test_every_status_change_writes_exactly_one_event(env):
    world, store, engine = env
    with store.tenant(1) as s:
        events = s.scalar(select(func.count()).select_from(OutboxEvent))
        versions = s.scalar(select(func.sum(RequirementState.version)))
    assert events == versions > 0
    engine.reindex(1)
    engine.refresh(1)                                   # nothing changed, so nothing new is reported
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(OutboxEvent)) == events


def test_a_crash_mid_dispatch_leaves_no_partial_work_and_nothing_twice(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    with pytest.raises(RuntimeError):
        dispatch(store, pack, 1, now, fail_after=3)
    first = dispatch(store, pack, 1, now)
    again = dispatch(store, pack, 1, now)
    assert first["events"] > 0 and again["events"] == 0
    with store.tenant(1) as s:
        open_tasks = [(t.engagement_id, t.requirement_id) for t in s.scalars(select(Task).where(Task.status == "open"))]
        assert open_tasks and len(open_tasks) == len(set(open_tasks))
        assert s.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.dispatched_at.is_(None))) == 0
        chased = {t.engagement_id for t in s.scalars(select(Task).where(Task.kind == "chase_supplier"))}
        reminded = {n.engagement_id for n in s.scalars(select(Notification))}
        assert chased and chased == reminded


def test_putting_the_evidence_right_closes_the_chase_and_releases_the_hold(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    holding = load_pack().actions
    with store.tenant(1) as s:
        def only_insurance_holds(eid):                 # so putting insurance right must release the hold outright
            return all(x.requirement_id == "insurance" or x.requirement_id not in holding.hold_requirements
                       or x.status not in holding.hold_statuses
                       for x in s.scalars(select(RequirementState).where(RequirementState.engagement_id == eid)))
        st = next((x for x in s.scalars(select(RequirementState).where(RequirementState.requirement_id == "insurance",
                                                                       RequirementState.status == Status.EXPIRED))
                   if only_insurance_holds(x.engagement_id)), None)
        assert st is not None, "the tiny world has an expired certificate that is its engagement's only hold"
        e = s.get(Engagement, st.engagement_id)
        eid, bu, supplier_id, name = e.id, e.business_unit_id, e.supplier_id, s.get(Supplier, e.supplier_id).legal_name
    dispatch(store, pack, 1, now)
    with store.tenant(1) as s:
        hold = s.scalar(select(Hold).where(Hold.engagement_id == eid, Hold.released_at.is_(None)))
        assert hold is not None and "insurance" in hold.requirements
        assert s.scalar(select(Task).where(Task.engagement_id == eid, Task.requirement_id == "insurance",
                                           Task.status == "open")).kind == "chase_supplier"
    data = renewed_certificate(name, 10_000_000, world.as_of, seed=1)
    engine.process(1, engine.ingest(1, bu, "renewal.pdf", data, "supplier_portal", now, claimed_supplier_id=supplier_id))
    dispatch(store, pack, 1, now + timedelta(hours=1))
    with store.tenant(1) as s:
        st = s.get(RequirementState, {"engagement_id": eid, "requirement_id": "insurance", "tenant_id": 1})
        assert st.status in (Status.SATISFIED, Status.SATISFIED_WITH_FINDINGS)
        assert all(t.status != "open" for t in s.scalars(select(Task).where(Task.engagement_id == eid,
                                                                           Task.requirement_id == "insurance")))
        assert s.scalar(select(Hold).where(Hold.engagement_id == eid, Hold.released_at.is_(None))) is None
        assert s.scalar(select(Hold).where(Hold.engagement_id == eid)).released_at is not None


def test_a_reminder_waits_out_its_cooldown(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    dispatch(store, pack, 1, now)
    with store.tenant(1) as s:
        first = s.scalars(select(Notification)).first()
        eid, rid = first.engagement_id, first.requirement_id

    def chase_again(key: str, at: datetime) -> Counter:
        with store.tenant(1) as s:
            s.add(OutboxEvent(key=key, kind="requirement.status_changed",
                              payload={"engagement": eid, "requirement": rid, "from": "EXPIRED", "to": "EXPIRED",
                                       "failing": [], "hints": [], "document": None}))
        return dispatch(store, pack, 1, at)
    assert chase_again("again:2d", now + timedelta(days=2))["reminder_suppressed"] == 1
    assert chase_again("again:8d", now + timedelta(days=8))["supplier_reminded"] == 1


def test_webhooks_are_signed_retried_and_processed_once(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    secret = new_secret(b"test")
    with store.tenant(1) as s:
        s.add(WebhookEndpoint(url="https://buyer.example/hooks", secret=secret))
    clock = [now]
    rx = Receiver(secret, lambda: clock[0], outage=0.3, ack_loss=0.3, seed=7)
    dispatch(store, pack, 1, now)
    for _ in range(40):
        deliver(store, 1, rx, clock[0])
        clock[0] += timedelta(minutes=30)
    with store.tenant(1) as s:
        outcome = Counter(d.status for d in s.scalars(select(Delivery)))
    assert outcome["pending"] == 0 and outcome["delivered"] > 0
    ids = [m["event"] + m["type"] for m in rx.processed]
    assert len(ids) == len(set(ids)) and rx.duplicates > 0 and rx.refused > 0
    body, ts = '{"a": 1}', int(now.timestamp())
    headers = sign(secret, "msg_1", ts, body)
    assert verify(secret, headers, body, ts)
    assert not verify(secret, headers, '{"a": 2}', ts)                    # tampered body
    assert not verify(secret, headers, body, ts + 3600)                    # replayed an hour later
    assert not verify(new_secret(b"other"), headers, body, ts)             # signed with another secret


def test_a_bulk_load_acts_on_each_requirements_final_state_only(env):
    world, store, engine = env
    counts = dispatch(store, load_pack(), 1, _now(world))
    assert counts["superseded"] > 0                     # intermediate statuses from the load were not acted on
    assert counts["task_closed"] == 0                   # so no task was opened only to be closed again
