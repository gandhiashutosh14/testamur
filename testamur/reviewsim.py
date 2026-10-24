"""Simulated reviewers on a world with ground truth.

Two scenarios, each on a freshly processed world:

1. **Work the queue.** A reviewer who knows the truth settles every item the engine hands over, the way a careful
   person would (truth.py, `resolved`). Measured: the effort by kind of decision, whether every status is right
   afterwards, and whether a re-index changes anything after that.
2. **Hold accuracy.** A fault is planted in one rule (the limitation period read as five years instead of three, as
   if a constant had slipped), the world is processed, and auditors answer the random audit sample. Measured: the
   false vouching the fault caused, whether and after how many audits each tenant's breaker tripped, the false
   vouching left once the check was demoted, and what the confirmations then cost.

The reviewer knows the truth, so this measures the machinery around people (decisions land, stick, and turn the
engine's "not sure" into the right status), not how good people are.
"""
from __future__ import annotations

import random
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select

from . import checks
from .engine import Engine, QueueItem
from .harness import git_stamp, process_world
from .model import VOUCHING, Status, Verdict
from .pack import load_pack
from .store import Document, RequirementState, Store, UploadRow
from .synth.truth import truth_check, truth_statuses
from .synth.world import World

REVIEWER, AUDITOR = "simulated-reviewer", "simulated-auditor"
FAULTS = {"limitation-years": "claim_not_time_barred"}


@contextmanager
def planted_fault(name: str | None):
    """A deliberately wrong rule, to see whether audits catch it. `limitation-years`: a default is compared with five
    years before the insolvency commencement date instead of three, so claims outside limitation pass."""
    if name is None:
        yield
        return
    if name not in FAULTS:
        raise ValueError(f"unknown fault {name}; known: {', '.join(FAULTS)}")
    original = checks.RULES["date_not_before"]

    def lenient(cid, rid, spec, ctx, p):
        if cid == FAULTS[name]:
            p = {**p, "offset_years": p.get("offset_years", 0) - 2}
        return original(cid, rid, spec, ctx, p)
    checks.RULES["date_not_before"] = lenient
    try:
        yield
    finally:
        checks.RULES["date_not_before"] = original


class Oracle:
    """Settles queue and audit items from the world's truth, the way a careful person would."""

    def __init__(self, world: World, engine: Engine):
        self.world, self.engine, self.pack = world, engine, engine.pack
        self.uploads = {u.id: u for u in world.uploads}
        self.claims = {e.id: e for e in world.claims}
        self.tenant_of = {x.id: x.tenant_id for x in world.creditors}

    def _upload(self, tenant_id: int, doc_id: int):
        with self.engine.store.tenant(tenant_id) as s:
            doc = s.get(Document, doc_id)
            ref = s.scalars(select(UploadRow.external_ref).where(UploadRow.sha256 == doc.sha256,
                                                                 UploadRow.matter_id == doc.matter_id)
                            .order_by(UploadRow.id)).first()
            holder = doc.creditor_id
        # the document's first upload in its own process stands for it: a copy carries the original's truth, and
        # its process is the one the document is evidence in
        return self.uploads[int(ref.split(":")[1])], holder

    def _belongs_here(self, owner: int, doc_type: str, matter_id: int) -> bool:
        """Evidence of one process counts there only: the owner (or a holder it acts as trustee for) must have a claim
        in the process the document was filed in. Evidence shared across processes belongs wherever it was filed."""
        if self.pack.doc_types[doc_type].evidence_scope == "creditor":
            return True
        acting_for = {owner} | {x.id for x in self.world.creditors if x.trustee_id == owner}
        return any(e.matter_id == matter_id and e.creditor_id in acting_for for e in self.world.claims)

    def verdict(self, tenant_id: int, item: QueueItem) -> Verdict | None:
        """What a careful person says about a check of this document; None if they could not say either."""
        u, holder = self._upload(tenant_id, item.document_id)
        e = self.claims[item.claim_id]
        r = truth_check(self.pack, self.world, item.check_id, item.requirement_id, e, u,
                        holder is not None and holder != e.creditor_id, resolved=True)
        return r.verdict if r.verdict in (Verdict.PASS, Verdict.FAIL) else None

    def settle(self, tenant_id: int, item: QueueItem) -> str:
        """Decide one item. Returns the kind of decision, or "left" if a person could not decide it either."""
        if item.kind in ("check", "audit"):
            v = self.verdict(tenant_id, item)
            if v is None:
                return "left"
            audit = item.kind == "audit"
            self.engine.review_check(tenant_id, item.claim_id, item.requirement_id, item.check_id, v,
                                     AUDITOR if audit else REVIEWER, kind="audit" if audit else "queue")
            if audit:
                said = Verdict.PASS if item.reason.startswith("the engine said PASS") else Verdict.FAIL
                return "audit_agreed" if said == v else "audit_overturned"
            return "confirm" if item.confirm else "check"
        u, _ = self._upload(tenant_id, item.document_id)
        t = u.truth
        if item.kind == "document_type":
            if t.doc_type == "unknown":
                self.engine.reject(tenant_id, item.document_id, REVIEWER, "not evidence for any claim")
                return "reject"
            self.engine.classify_document(tenant_id, item.document_id, t.doc_type, REVIEWER)
            return "classify"
        if item.kind == "document_owner":
            if t.owner_creditor_id is not None and self.tenant_of.get(t.owner_creditor_id) == tenant_id:
                if self._belongs_here(t.owner_creditor_id, t.doc_type, u.matter_id):
                    self.engine.assign(tenant_id, item.document_id, t.owner_creditor_id, REVIEWER)
                    return "assign"
                self.engine.reject(tenant_id, item.document_id, REVIEWER,
                                   "filed in a process in which the creditor has no claim")
                return "reject"
            self.engine.reject(tenant_id, item.document_id, REVIEWER, "not evidence for any of our creditors")
            return "reject"
        if item.kind == "document_security":
            self.engine.clear(tenant_id, item.document_id, REVIEWER, "read in full; the embedded instruction is ignored")
            return "clear"
        raise ValueError(f"unknown item kind {item.kind}")


def compare(store: Store, truth: dict) -> Counter:
    with store.system() as s:
        states = {(r.claim_id, r.requirement_id): Status(r.status) for r in s.scalars(select(RequirementState))}
    out = Counter()
    for key, t in truth.items():
        es, ts = states.get(key), t["status"]
        if es == ts:
            out["exact"] += 1
        elif es == Status.NEEDS_REVIEW:
            out["deferred"] += 1
        elif es in VOUCHING and ts not in VOUCHING:
            out["false_vouching"] += 1
        elif es not in VOUCHING and ts in VOUCHING:
            out["false_alarm"] += 1
        else:
            out["other"] += 1
    return out


def _statuses(store: Store) -> dict:
    with store.system() as s:
        return {(r.claim_id, r.requirement_id): r.status for r in s.scalars(select(RequirementState))}


def work_queue(world: World, engine: Engine, oracle: Oracle, limit: int = 5000) -> Counter:
    """Settle the queue one item at a time (a decision can change or remove the items after it), until it is empty
    or holds only what a person could not decide either."""
    effort = Counter()
    for t in world.tenants:
        left: set[tuple] = set()
        seen: set[tuple] = set()
        for _ in range(limit):
            items = [i for i in engine.queue(t.id) if not i.retrying
                     and (i.kind, i.document_id, i.claim_id, i.requirement_id, i.check_id) not in left]
            if not items:
                break
            item = items[0]
            key = (item.kind, item.document_id, item.claim_id, item.requirement_id, item.check_id)
            if key in seen:                    # settled once already and back again: a decision that did not land
                effort["did_not_land"] += 1
                left.add(key)
                continue
            seen.add(key)
            kind = oracle.settle(t.id, item)
            effort[kind] += 1
            if kind == "left":
                left.add(key)
    return effort


def queue_scenario(world_dir: str | Path, db: str | Path, model=None) -> dict:
    world, store, engine, seconds = process_world(world_dir, db, model)
    pack = engine.pack
    plain, resolved = truth_statuses(world, pack), truth_statuses(world, pack, resolved=True)
    before = {"vs_truth": dict(compare(store, plain)), "vs_settled_truth": dict(compare(store, resolved)),
              "queue": dict(Counter(i.kind + ("/confirm" if i.confirm else "") for t in world.tenants
                                    for i in engine.queue(t.id)))}
    effort = work_queue(world, engine, Oracle(world, engine))
    after = dict(compare(store, resolved))
    settled = _statuses(store)
    for t in world.tenants:
        engine.reindex(t.id)
        engine.refresh(t.id)
    changed = sum(1 for k, v in _statuses(store).items() if settled.get(k) != v)
    return {"pairs": len(resolved), "seconds": round(seconds, 1), "before": before, "effort": dict(effort),
            "after": after, "changed_by_reindex": changed}


def accuracy_scenario(world_dir: str | Path, db: str | Path, model=None, fault: str = "limitation-years",
                      audit_rate: float | None = None, seed: int = 1) -> dict:
    pack = load_pack().model_copy(deep=True)
    if audit_rate is not None:
        pack.audit.audit_rate = {k: audit_rate for k in pack.audit.audit_rate}
    check = FAULTS[fault]
    with planted_fault(fault):
        world, store, engine, seconds = process_world(world_dir, db, model, pack=pack)
        plain = truth_statuses(world, pack)
        before = dict(compare(store, plain))
        false_passes = _false_passes(world, store, engine, check, plain)
        oracle, tenants = Oracle(world, engine), []
        for t in world.tenants:
            sample = engine.audit_sample(t.id, seed=str(seed))
            random.Random(seed).shuffle(sample)
            row = {"tenant": t.id, "false_pass_verdicts": false_passes.get(t.id, 0), "audits": len(sample),
                   "audits_of_the_check": sum(i.check_id == check for i in sample), "tripped_after": None,
                   "overturned": 0}
            for n, item in enumerate(sample, 1):
                kind = oracle.settle(t.id, item)
                row["overturned"] += kind == "audit_overturned"
                if row["tripped_after"] is None and engine.health(t.id)[check].state == "breached":
                    row["tripped_after"] = n
            tenants.append(row)
        after_audit = dict(compare(store, plain))
        effort = work_queue(world, engine, oracle)
        final = dict(compare(store, truth_statuses(world, pack, resolved=True)))
    rates = (0.02, 0.05, 0.10, 0.25, 0.50)
    detection = {f"{r:.0%}": {str(x["tenant"]): round(1 - (1 - r) ** x["false_pass_verdicts"], 3) for x in tenants}
                 for r in rates}
    return {"fault": fault, "check": check, "audit_rate": pack.audit.audit_rate, "seed": seed,
            "seconds": round(seconds, 1), "before": before, "tenants": tenants, "after_audit": after_audit,
            "effort_after_audit": dict(effort), "final": final, "detection_probability": detection,
            "pairs": len(plain)}


def _false_passes(world: World, store: Store, engine: Engine, check: str, truth: dict) -> dict[int, int]:
    """Per tenant: the engine's PASS verdicts of `check` on current documents where the truth gives no PASS (a FAIL,
    or a question only a person can settle)."""
    out: Counter = Counter()
    tenant_of = {e.id: e.tenant_id for e in world.claims}
    for key, tv in truth.items():
        if check not in tv["checks"]:
            continue
        _, results = engine.verdicts(tenant_of[key[0]], *key)
        mine = next((r for r in results if r.check_id == check), None)
        if mine is not None and mine.verdict == Verdict.PASS and tv["checks"][check] in (Verdict.FAIL, Verdict.ABSTAIN):
            out[tenant_of[key[0]]] += 1
    return dict(out)


def render(q: dict | None, a: dict | None, meta: dict) -> str:
    out = ["# Review simulation", "",
           f"Commit `{meta['commit']}`, command `{meta['command']}`. World seed {meta['seed']} ({meta['size']}). "
           "The reviewer and the auditors know the ground truth: this measures the machinery around people, not "
           "people.", ""]
    if q:
        b, e, f = q["before"], q["effort"], q["after"]
        out += ["## 1. Working the queue", "",
                f"{q['pairs']} claim-requirement pairs. Before review, against the truth as a careful person "
                f"would settle it: {b['vs_settled_truth'].get('exact', 0)} exact, "
                f"{b['vs_settled_truth'].get('deferred', 0)} waiting for a person, "
                f"{b['vs_settled_truth'].get('false_vouching', 0)} false vouching.", "",
                "| Decision | Count |", "|---|---|"]
        out += [f"| {k} | {v} |" for k, v in sorted(e.items())]
        out += ["", f"After review: {f.get('exact', 0)} of {q['pairs']} exact, {f.get('deferred', 0)} still waiting, "
                f"{f.get('false_vouching', 0)} false vouching, {f.get('false_alarm', 0)} false alarm, "
                f"{f.get('other', 0)} other. A full re-index afterwards changed {q['changed_by_reindex']} statuses "
                "(decisions are inputs, so it should change none).", ""]
    if a:
        out += ["## 2. Holding accuracy against a planted fault", "",
                f"Fault `{a['fault']}`: the rule behind `{a['check']}` reads the limitation period as five years "
                f"instead of three. Audit rate by severity: {a['audit_rate']}; seed {a['seed']}.", "",
                f"Before any audit, against the truth: {a['before'].get('exact', 0)} exact, "
                f"**{a['before'].get('false_vouching', 0)} false vouching** caused by the fault.", "",
                "| Tenant | False PASS verdicts | Audits drawn | ... of the faulty check | Overturned | Breaker tripped after |",
                "|---|---|---|---|---|---|"]
        for x in a["tenants"]:
            out.append(f"| {x['tenant']} | {x['false_pass_verdicts']} | {x['audits']} | {x['audits_of_the_check']} | "
                       f"{x['overturned']} | {x['tripped_after'] if x['tripped_after'] else 'not tripped'} |")
        aa, ef, fi = a["after_audit"], a["effort_after_audit"], a["final"]
        out += ["", f"After the audits: **{aa.get('false_vouching', 0)} false vouching** left "
                f"({aa.get('deferred', 0)} statuses now wait for a person). Working the queue then took: "
                + ", ".join(f"{k} {v}" for k, v in sorted(ef.items())) + ". "
                f"Finally {fi.get('exact', 0)} of {a['pairs']} statuses exact against the settled truth, "
                f"{fi.get('false_vouching', 0)} false vouching.", "",
                "How likely a sample at each rate is to draw at least one of the fault's false PASS verdicts "
                "(1 - (1 - rate)^n, per tenant):", "",
                "| Audit rate | " + " | ".join(f"tenant {t}" for t in a["detection_probability"]["2%"]) + " |",
                "|---|" + "---|" * len(a["detection_probability"]["2%"])]
        for rate, by_t in a["detection_probability"].items():
            out.append(f"| {rate} | " + " | ".join(f"{p:.0%}" for p in by_t.values()) + " |")
        out += ["", "A breaker that has not tripped is not a pass: the fault keeps producing false PASS verdicts "
                "until a sample draws one, so the audit rate is a choice between review effort and time to "
                "detection.", ""]
    return "\n".join(out)


def write(q: dict | None, a: dict | None, path: str | Path, command: str, world_seed: int, size: str) -> None:
    import json
    meta = {"commit": git_stamp(Path.cwd()), "command": command, "seed": world_seed, "size": size}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps({"meta": meta, "queue": q, "accuracy": a}, indent=2, default=str),
                                         encoding="utf-8")
    path.write_text(render(q, a, meta), encoding="utf-8")
