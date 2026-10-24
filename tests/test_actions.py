"""From finding to action: one outbox event per status or permission change; dispatching turns events into single
tasks, reminders and holds, even across a crash; putting the evidence right undoes them; webhooks are signed, retried
and processed once."""
from collections import Counter
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from testamur.actions import Receiver, deliver, dispatch, new_secret, sign, verify
from testamur.actionsim import renewed_authority
from testamur.model import Status
from testamur.pack import load_pack
from testamur.store import (
    Claim,
    ClaimPermission,
    Creditor,
    Delivery,
    Hold,
    Notification,
    OutboxEvent,
    RequirementState,
    Task,
    WebhookEndpoint,
)


def _now(world):
    return datetime.combine(world.as_of, time(9))


def test_every_status_and_permission_change_writes_exactly_one_event(env):
    world, store, engine = env
    with store.tenant(1) as s:
        events = s.scalar(select(func.count()).select_from(OutboxEvent))
        versions = s.scalar(select(func.sum(RequirementState.version)))
        permissions = s.scalar(select(func.sum(ClaimPermission.version)))
    assert events == versions + permissions and versions > 0 and permissions > 0
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
        open_tasks = [(t.claim_id, t.requirement_id) for t in s.scalars(select(Task).where(Task.status == "open"))]
        assert open_tasks and len(open_tasks) == len(set(open_tasks))
        assert s.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.dispatched_at.is_(None))) == 0
        chased = {t.claim_id for t in s.scalars(select(Task).where(Task.kind == "chase_creditor"))}
        reminded = {n.claim_id for n in s.scalars(select(Notification))}
        assert chased and chased == reminded


def _lapsed_authority_alone(store, world):
    """A claim whose vote is withheld only because its authority lapsed, in whichever tenant has one: its proof of
    claim permits a vote, so renewing the authority must permit it and release the hold."""
    proof_ok = next(c.statuses for c in load_pack().permissions["vote"].requires if c.requirement == "proof_of_claim")
    for t in world.tenants:
        with store.tenant(t.id) as s:
            for st in s.scalars(select(RequirementState).where(RequirementState.requirement_id == "authority",
                                                               RequirementState.status == Status.EXPIRED)):
                proof = s.get(RequirementState, {"claim_id": st.claim_id, "requirement_id": "proof_of_claim",
                                                 "tenant_id": t.id})
                if proof is not None and proof.status in proof_ok:
                    e = s.get(Claim, st.claim_id)
                    return t.id, e.id, e.matter_id, e.creditor_id, s.get(Creditor, e.creditor_id).legal_name
    raise AssertionError("the tiny world has a claim whose vote waits only on an expired authority")


def test_putting_the_evidence_right_closes_the_chase_and_releases_the_hold(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    tenant, eid, matter, creditor_id, name = _lapsed_authority_alone(store, world)
    dispatch(store, pack, tenant, now)
    with store.tenant(tenant) as s:
        hold = s.scalar(select(Hold).where(Hold.claim_id == eid, Hold.action == "vote", Hold.released_at.is_(None)))
        assert hold is not None and "authority is EXPIRED" in hold.reasons
        assert s.scalar(select(Task).where(Task.claim_id == eid, Task.requirement_id == "authority",
                                           Task.status == "open")).kind == "chase_creditor"
    data = renewed_authority(name, world.as_of, seed=1)
    engine.process(tenant, engine.ingest(tenant, matter, "renewal.pdf", data, "creditor_portal", now,
                                         declared_creditor_id=creditor_id))
    dispatch(store, pack, tenant, now + timedelta(hours=1))
    with store.tenant(tenant) as s:
        st = s.get(RequirementState, {"claim_id": eid, "requirement_id": "authority", "tenant_id": tenant})
        assert st.status in (Status.SATISFIED, Status.SATISFIED_WITH_FINDINGS)
        assert all(t.status != "open" for t in s.scalars(select(Task).where(Task.claim_id == eid,
                                                                           Task.requirement_id == "authority")))
        assert s.get(ClaimPermission, {"claim_id": eid, "action": "vote", "tenant_id": tenant}).granted
        assert s.scalar(select(Hold).where(Hold.claim_id == eid, Hold.action == "vote",
                                           Hold.released_at.is_(None))) is None
        assert s.scalar(select(Hold).where(Hold.claim_id == eid, Hold.action == "vote")).released_at is not None


def test_a_reminder_waits_out_its_cooldown(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    dispatch(store, pack, 1, now)
    with store.tenant(1) as s:
        first = s.scalars(select(Notification)).first()
        eid, rid = first.claim_id, first.requirement_id

    def chase_again(key: str, at: datetime) -> Counter:
        with store.tenant(1) as s:
            s.add(OutboxEvent(key=key, kind="requirement.status_changed",
                              payload={"claim": eid, "requirement": rid, "from": "EXPIRED", "to": "EXPIRED",
                                       "failing": [], "hints": [], "document": None}))
        return dispatch(store, pack, 1, at)
    assert chase_again("again:2d", now + timedelta(days=2))["reminder_suppressed"] == 1
    assert chase_again("again:8d", now + timedelta(days=8))["creditor_reminded"] == 1


def test_webhooks_are_signed_retried_and_processed_once(env):
    world, store, engine = env
    pack, now = load_pack(), _now(world)
    secret = new_secret(b"test")
    with store.tenant(1) as s:
        s.add(WebhookEndpoint(url="https://firm.example/hooks", secret=secret))
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
    counts = [dispatch(store, load_pack(), t.id, _now(world)) for t in world.tenants]
    assert sum(c["superseded"] for c in counts) > 0      # intermediate statuses from the load were not acted on
    assert all(c["task_closed"] == 0 for c in counts)   # so no task was opened only to be closed again


def test_the_simulations_report_renders_what_the_simulation_returns(tmp_path):
    """The report reads the simulation's own result: an invariant renamed in one and not the other once broke the
    regeneration of every report."""
    from testamur.actionsim import render, run
    from testamur.synth.world import build_world
    build_world(seed=11, size="tiny", out=tmp_path / "w", digital_only=True)
    r = run(tmp_path / "w", tmp_path / "a.db", days=3)
    text = render(r, {"commit": "test", "command": "test", "seed": 11, "size": "tiny"})
    assert "At most one open hold per claim and action: True" in text
