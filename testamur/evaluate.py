"""The comparison the agents are claimed on: the rules alone, one agent (the investigator), and the
investigator-verifier pair, on cases written by hand for it (evals/holdout/cases.yaml), never by the world generator.

Each case is one claim with the documents it files and a target: a check, or a requirement's status, with the outputs
that are right for it. Every arm reads the same database, built from the cases alone. The engine's own clause tier is
off, so the agents are the only model that reads the documents.

- **Rules**: the engine's verdict on the target (ABSTAIN is "person"); for a requirement, its status (a vouching
  status is PASS, NEEDS_REVIEW is "person", any other is FAIL).
- **Single**: the investigator alone. Its output is the coordinator's recommendation on the target: a question for a
  person, or a proposed verdict; where it recommends nothing, the rules' output stands.
- **Pair**: the investigator and the verifier. At temperature 0 the pair's investigator asks exactly what the single
  arm's asked, so its answers come from the ledger; the pair's latency is the investigator's (measured in the single
  arm) plus the verifier's.

Per arm: task success (the output is one of the right ones), false PASS (PASS where PASS is not right), false FAIL,
the share sent to a person; for the agent arms, citations that held, tool calls refused, tool calls that reached a
document outside the case's own (a permission violation, checked here independently of the gateway), latency, tokens
and the model's cost. `faults()` adds scripted fault-injection scenarios: deterministic, no model.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from .engine import Engine
from .model import VOUCHING, Status, Verdict
from .pack import load_pack
from .store import Claim, Creditor, Document, Matter, Store, Tenant, UploadRow
from .synth.render import DocContent, render_pdf

ARMS = ("rules", "single", "pair")
TENANT, MATTER = 1, 1


@dataclass
class Case:
    id: str
    category: str
    creditor: str
    claim: dict
    documents: list[dict]
    target: dict
    truth: list[str]
    why: str


@dataclass
class HoldoutSet:
    meta: dict
    cases: list[Case]
    digest: str                       # of the file's bytes: the version of the set a result is about


def load(path: str | Path) -> HoldoutSet:
    raw = Path(path).read_bytes()
    data = yaml.safe_load(raw)
    cases = [Case(**c) for c in data.pop("cases")]
    for c in cases:
        bad = set(c.truth) - {"PASS", "FAIL", "person"}
        if bad or not c.truth:
            raise ValueError(f"case {c.id}: truth must be PASS, FAIL or person, not {sorted(bad)}")
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    return HoldoutSet(data, cases, hashlib.sha256(raw).hexdigest()[:16])


# ---------------------------------------------------------------------------------------------- the database

@dataclass
class Built:
    store: Store
    engine: Engine
    claims: dict[str, int]                         # case id -> claim id
    documents: dict[str, set[int]]                 # case id -> the documents its uploads became


def build(hs: HoldoutSet, root: str | Path, cases: list[Case] | None = None) -> Built:
    """One firm, one process, one creditor and claim per case, and only the documents the cases file."""
    root = Path(root)
    if (root / "holdout.db").exists():                   # an earlier run's database: start again
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    m = hs.meta
    as_of = _date(m["as_of"])
    p = m["process"]
    store = Store(f"sqlite:///{(root / 'holdout.db').as_posix()}")
    cases = hs.cases if cases is None else cases
    with store.system() as s:
        s.add(Tenant(id=TENANT, name=m["firm"]))
        s.flush()
        s.add(Matter(id=MATTER, tenant_id=TENANT, name=p["debtor"], debtor_cin=p.get("cin", ""),
                     commencement=_date(p["commencement"]), announced=_date(p["announced"]),
                     claims_due=_date(p["claims_due"])))
        s.flush()
        for i, c in enumerate(hs.cases, 1):              # every case's claim exists, so ids do not depend on a shard
            s.add(Creditor(id=i, tenant_id=TENANT, legal_name=c.creditor))
        s.flush()
        for i, c in enumerate(hs.cases, 1):
            s.add(Claim(id=i, tenant_id=TENANT, matter_id=MATTER, creditor_id=i, tier=c.claim["tier"],
                        claim_type=c.claim["type"], facility_type=c.claim["facility"],
                        book_balance=int(c.claim["books"]), sanctioned=int(c.claim["sanctioned"])))
    engine = Engine(store, load_pack(), as_of, root / "blobs")
    claims = {c.id: i for i, c in enumerate(hs.cases, 1)}
    uploaded: dict[str, list[int]] = {}
    when = datetime.combine(as_of - timedelta(days=10), datetime.min.time()) + timedelta(hours=10)
    for c in cases:
        values = {"creditor": c.creditor, "debtor": p["debtor"]}
        for d in c.documents:
            pages = [[tuple(str(x).format(**values) for x in item) for item in page] for page in d["pages"]]
            data = render_pdf(DocContent(pages), random.Random(_seed(c.id, d["name"])))
            uid = engine.ingest(TENANT, MATTER, d["name"], data, "creditor_portal", when,
                                declared_creditor_id=claims[c.id], external_ref=f"holdout:{c.id}")
            engine.process(TENANT, uid)
            uploaded.setdefault(c.id, []).append(uid)
    engine.refresh(TENANT)
    documents: dict[str, set[int]] = {}
    with store.tenant(TENANT) as s:
        for cid, uids in uploaded.items():
            shas = {s.get(UploadRow, u).sha256 for u in uids}
            documents[cid] = {d.id for d in s.scalars(select(Document).where(Document.sha256.in_(shas)))}
    return Built(store, engine, claims, documents)


def _date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------------------------- outputs

def rules_output(engine: Engine, claim_id: int, target: dict) -> str:
    st, results = engine.verdicts(TENANT, claim_id, target["requirement"])
    if target.get("check"):
        r = next((r for r in results if r.check_id == target["check"]), None)
        if r is None:
            return "person"                              # not judged (a quarantined document): a person reads it
        return {Verdict.PASS: "PASS", Verdict.FAIL: "FAIL"}.get(r.verdict, "person")
    if st is None:
        return "FAIL"
    if st.status in VOUCHING:
        return "PASS"
    return "person" if st.status == Status.NEEDS_REVIEW else "FAIL"


def agent_output(run: dict, target: dict, rules: str) -> str:
    """A question for a person on the target wins over a proposal; a proposal over the rules."""
    mine = [r for r in run.get("recommendations", []) if r["requirement"] == target["requirement"]
            and (r.get("check") in (None, target.get("check")) if target.get("check") else True)]
    if any(r["action"] == "ask_person" for r in mine):
        return "person"
    proposed = {r["verdict"] for r in mine if r["action"] == "propose_verdict" and
                (not target.get("check") or r.get("check") == target["check"])}
    if len(proposed) == 1:
        return proposed.pop()
    return "person" if proposed else rules


# ---------------------------------------------------------------------------------------------- running

@dataclass
class CaseResult:
    case: str
    category: str
    truth: list[str]
    outputs: dict[str, str] = field(default_factory=dict)
    runs: dict[str, dict] = field(default_factory=dict)       # per agent arm: what the run did


def evaluate(hs: HoldoutSet, root: str | Path, model, *, arms=ARMS, shard: tuple[int, int] = (0, 1),
             budgets=None) -> dict[str, Any]:
    """Run the arms over this shard's cases. `model` is the agents' model (None: the agent arms are skipped). A shard's
    database holds its own cases' documents only, and their ids are in the agents' prompts: a replay of a recording
    must use the shards the recording was made in."""
    from .investigate import Budgets, Investigations, sqlite_checkpointer
    from .investigate.agent import Budget
    index, count = shard
    mine = [c for i, c in enumerate(hs.cases) if i % count == index]
    built = build(hs, root, mine)
    # a generous clock, so that only steps and tokens bound a run: a replay then takes the recorded path exactly
    budgets = budgets or Budgets(investigator=Budget(steps=8, seconds=3_600), verifier=Budget(steps=7, seconds=3_600),
                                 tool_calls=10)
    saver, conn = sqlite_checkpointer(Path(root) / "checkpoints.sqlite")
    flows = Investigations(built.engine, model, saver, budgets) if model is not None else None
    results = []
    try:
        for c in mine:
            claim = built.claims[c.id]
            r = CaseResult(c.id, c.category, c.truth)
            r.outputs["rules"] = rules_output(built.engine, claim, c.target)
            for arm in ("single", "pair"):
                if arm not in arms or flows is None:
                    continue
                t0 = time.perf_counter()
                run = flows.start(TENANT, claim, "evaluation", mode=arm)
                wall = time.perf_counter() - t0
                r.outputs[arm] = agent_output(run, c.target, r.outputs["rules"])
                r.runs[arm] = _summarise_run(run, wall, built.documents.get(c.id, set()))
            results.append(r)
    finally:
        conn.close()
        built.store.engine.dispose()
    from .harness import git_stamp
    return {"set": hs.digest, "shard": list(shard), "cases": [asdict(r) for r in results],
            "model": getattr(model, "model_id", None), "fingerprint": getattr(model, "fingerprint", None),
            "commit": git_stamp(Path.cwd())}


def _summarise_run(run: dict, wall: float, own: set[int]) -> dict:
    tools = [e for e in run["trace"] if e["kind"] == "tool"]
    agents = {e["role"]: e for e in run["trace"] if e["kind"] == "agent"}
    stats = run.get("stats") or {}
    reached = [e for e in tools if e["outcome"] == "ok" and isinstance(e.get("args"), dict)
               and "document_id" in e["args"] and _int(e["args"]["document_id"]) not in own]
    return {"status": run["status"], "seconds": round(wall, 2), "tokens": run.get("tokens") or 0,
            "agent_seconds": {role: round((e.get("ms") or 0) / 1000, 2) for role, e in agents.items()},
            "stops": {role: e["outcome"] for role, e in agents.items()},
            "steps": {role: (e.get("detail") or {}).get("steps") for role, e in agents.items()},
            "cached_calls": {role: (e.get("detail") or {}).get("cached_calls") for role, e in agents.items()},
            "tool_calls": len(tools), "refused": sum(e["outcome"] == "denied" for e in tools),
            "violations": len(reached), "citations": stats.get("citations", 0),
            "citations_valid": stats.get("citations_valid", 0),
            "recommendations": [{k: r.get(k) for k in ("requirement", "check", "action", "verdict", "agreement")}
                                for r in run.get("recommendations", [])]}


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------- the numbers

def merge(parts: list[dict]) -> dict:
    sets = {p["set"] for p in parts}
    if len(sets) != 1:
        raise ValueError(f"results of different case sets: {sorted(sets)}")
    fingerprints = {p.get("fingerprint") for p in parts if p.get("fingerprint")}
    if len(fingerprints) > 1:
        raise ValueError(f"results of different models: {sorted(fingerprints)}")
    commits = sorted({p["commit"] for p in parts if p.get("commit")})
    if len(commits) > 1:
        raise ValueError(f"results of different commits: {commits}")
    cases = sorted((c for p in parts for c in p["cases"]), key=lambda c: c["case"])
    return {"set": sets.pop(), "cases": cases, "model": next((p["model"] for p in parts if p.get("model")), None),
            "fingerprint": next(iter(fingerprints), None), "commit": commits[0] if commits else None}


def summarise(merged: dict) -> dict[str, dict]:
    out = {}
    cases = merged["cases"]
    for arm in ARMS:
        rows = [c for c in cases if arm in c["outputs"]]
        if not rows:
            continue
        n = len(rows)
        right = sum(c["outputs"][arm] in c["truth"] for c in rows)
        false_pass = sum(c["outputs"][arm] == "PASS" and "PASS" not in c["truth"] for c in rows)
        false_fail = sum(c["outputs"][arm] == "FAIL" and "FAIL" not in c["truth"] for c in rows)
        person = sum(c["outputs"][arm] == "person" for c in rows)
        m = {"cases": n, "success": right, "false_pass": false_pass, "false_fail": false_fail, "person": person,
             "pass_cases": sum("PASS" in c["truth"] for c in rows)}
        if arm != "rules":
            runs = [c["runs"][arm] for c in rows]
            if arm == "pair":           # the pair's investigator answered from the single arm's ledger
                seconds = [c["runs"].get("single", {}).get("agent_seconds", {}).get("investigator", 0) +
                           c["runs"]["pair"]["agent_seconds"].get("verifier", 0) for c in rows]
            else:
                seconds = [r["seconds"] for r in runs]
            m |= {"citations": sum(r["citations"] for r in runs),
                  "citations_valid": sum(r["citations_valid"] for r in runs),
                  "tool_calls": sum(r["tool_calls"] for r in runs), "refused": sum(r["refused"] for r in runs),
                  "violations": sum(r["violations"] for r in runs),
                  "p50_seconds": round(statistics.median(seconds), 1),
                  "p95_seconds": round(_quantile(seconds, 0.95), 1),
                  "tokens_per_case": round(statistics.mean(r["tokens"] for r in runs)),
                  "stops": _count(stop for r in runs for stop in r["stops"].values())}
        out[arm] = m
    return out


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def by_category(merged: dict) -> dict[str, dict[str, str]]:
    cats: dict[str, dict[str, list[int]]] = {}
    for c in merged["cases"]:
        for arm, o in c["outputs"].items():
            cats.setdefault(c["category"], {}).setdefault(arm, [0, 0])
            cats[c["category"]][arm][0] += o in c["truth"]
            cats[c["category"]][arm][1] += 1
    return {cat: {arm: f"{k} of {n}" for arm, (k, n) in arms.items()} for cat, arms in cats.items()}


# ---------------------------------------------------------------------------------------------- faults

class _Scripted:
    """A scripted model for the fault scenarios: deterministic turns, read from the system prompt's role."""
    model_id, fingerprint = "scripted-faults", "scripted"

    def __init__(self, investigator: list, verifier: list | None = None, fail_at: tuple[str, int] | None = None):
        self.scripts = {"investigator": investigator, "verifier": verifier or []}
        self.calls = {"investigator": 0, "verifier": 0}
        self.fail_at = fail_at

    def chat(self, messages, max_tokens=200, schema=None):
        from .modeltier import Reply
        role = "verifier" if messages[0]["content"].startswith("You are the verifier") else "investigator"
        n = self.calls[role]
        self.calls[role] += 1
        if self.fail_at == (role, n):
            raise TimeoutError("the model did not answer in time")
        script = self.scripts[role]
        turn = script[min(n, len(script) - 1)]
        return Reply(json.dumps(turn(messages) if callable(turn) else turn), 100, 30)


def _call(tool, **args):
    return {"note": tool, "action": "call_tool", "tool": tool, "args": args}


def _finish(result):
    return {"note": "done", "action": "finish", "result": result}


def faults(hs: HoldoutSet, root: str | Path) -> list[dict]:
    """Five ways an investigation can go wrong, each forced on purpose with a scripted model, and what the platform
    did: a model that times out, an agent reaching outside its claim, approval of evidence that changed, a decision
    delivered twice, and a worker that finishes after its lease was taken over."""
    from . import agents, jobs
    from .investigate import Investigations, sqlite_checkpointer
    from .worker import work
    root = Path(root)
    case = next(c for c in hs.cases if c.id == "conditional-approval-recorded-on-the-letter")
    other = next(c for c in hs.cases if c.id == "control-plain-grant")
    out = []

    def fresh(name):
        built = build(hs, root / name, [case, other])
        saver, conn = sqlite_checkpointer(root / name / "checkpoints.sqlite")
        return built, saver, conn

    def packet_for(built):
        doc = min(built.documents[case.id])
        with built.store.tenant(TENANT) as s:
            pages = built.engine._extraction(s, TENANT, s.get(Document, doc).sha256).pages
        approval = next(line.text for p in pages for line in p.lines if "Extract of the minutes" in line.text)
        cite = [{"document_id": doc, "page": 1, "quote": approval[:60]}]
        finding = {"requirement": "authority", "check": "auth_covers_voting", "stance": "supports",
                   "statement": "the board approved the vote, as the same page records", "citations": cite}
        investigator = [_call("read_page", document_id=doc, page=1),
                        _finish({"findings": [finding], "open_questions": []})]
        verifier = [_call("read_page", document_id=doc, page=1),
                    _finish({"reviews": [{"finding": 0, "assessment": "supported", "reason": "on page 1",
                                          "citations": cite}], "additional": []})]
        return investigator, verifier

    # 1. the model times out mid-investigation
    built, saver, conn = fresh("timeout")
    try:
        inv, ver = packet_for(built)
        run = Investigations(built.engine, _Scripted(inv, ver, fail_at=("investigator", 1)), saver).start(
            TENANT, built.claims[case.id], "evaluation")
        stop = next(e["outcome"] for e in run["trace"] if e["kind"] == "agent")
        out.append({"scenario": "the model times out during the investigation",
                    "expected": "the agent stops, nothing is proposed, the claim stays with a person",
                    "observed": f"agent stopped ({stop}); run {run['status']}; "
                                f"{sum(1 for r in run['recommendations'] if r.get('proposal_id'))} proposals",
                    "held": run["status"] == "needs_person" and "TimeoutError" in stop and not any(
                        r.get("proposal_id") for r in run["recommendations"])})
    finally:
        conn.close()
        built.store.engine.dispose()

    # 2. an agent asks for documents outside its claim
    built, saver, conn = fresh("scope")
    try:
        theirs = min(built.documents[other.id])
        run = Investigations(built.engine, _Scripted([_call("read_page", document_id=theirs, page=1),
                                                      _call("provenance", document_id=theirs),
                                                      _finish({"findings": []})]), saver).start(
            TENANT, built.claims[case.id], "evaluation", mode="single")
        tools = [e for e in run["trace"] if e["kind"] == "tool"]
        out.append({"scenario": "an agent asks for another claim's document, and for a tool it does not hold",
                    "expected": "both refused, recorded in the trace, nothing read",
                    "observed": ", ".join(f"{e['tool']}: {e['outcome']}" for e in tools),
                    "held": [e["outcome"] for e in tools] == ["denied", "denied"]})
    finally:
        conn.close()
        built.store.engine.dispose()

    # 3. the evidence changes between the proposal and the approval
    built, saver, conn = fresh("stale")
    try:
        inv, ver = packet_for(built)
        flows = Investigations(built.engine, _Scripted(inv, ver), saver)
        run = flows.start(TENANT, built.claims[case.id], "evaluation")
        rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
        page = [("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", case.creditor),
                ("kv", "Authorised representative", "Anjali Menon, Senior Manager"),
                ("kv", "Powers", "To attend meetings of the committee of creditors only"),
                ("kv", "Effective from", "2026-09-15"), ("kv", "Valid until", "2027-09-14"),
                ("kv", "Date of issue", "2026-09-15")]
        uid = built.engine.ingest(TENANT, MATTER, "authority-new.pdf", render_pdf(DocContent([page]),
                                  random.Random(5)), "creditor_portal", datetime(2026, 9, 25, 10),
                                  declared_creditor_id=built.claims[case.id])
        built.engine.process(TENANT, uid)
        done = flows.resume(TENANT, run["run"], [{"proposal": rec["proposal_id"], "approve": True,
                                                  "digest": rec["digest"]}], by="reviewer")
        out.append({"scenario": "a newer authority arrives after the proposal and before the approval",
                    "expected": "the approval applies nothing and is marked stale",
                    "observed": f"run {done['status']}; outcome {done['outcomes'][0]['outcome']}",
                    "held": done["status"] == "stale" and done["outcomes"][0]["outcome"] == "stale"})
    finally:
        conn.close()
        built.store.engine.dispose()

    # 4. the same decision is delivered twice
    built, saver, conn = fresh("twice")
    try:
        inv, ver = packet_for(built)
        flows = Investigations(built.engine, _Scripted(inv, ver), saver)
        run = flows.start(TENANT, built.claims[case.id], "evaluation")
        rec = next(r for r in run["recommendations"] if r.get("proposal_id"))
        decision = [{"proposal": rec["proposal_id"], "approve": True, "digest": rec["digest"]}]
        first = flows.resume(TENANT, run["run"], decision, by="reviewer")
        try:
            flows.resume(TENANT, run["run"], decision, by="reviewer")
            second = "accepted"
        except ValueError as exc:
            second = f"refused ({exc})"
        try:
            agents.decide(built.engine, TENANT, rec["proposal_id"], "reviewer", True, seen=rec["digest"])
            third = "accepted"
        except ValueError as exc:
            third = f"refused ({exc})"
        out.append({"scenario": "a reviewer's approval is delivered twice, once more around the run",
                    "expected": "applied once; both repeats refused",
                    "observed": f"first: {first['status']}; again on the run: {second}; directly: {third}",
                    "held": first["status"] == "applied" and second.startswith("refused") and
                    third.startswith("refused")})
    finally:
        conn.close()
        built.store.engine.dispose()

    # 5. a worker finishes after its lease was taken over
    built, saver, conn = fresh("lease")
    try:
        inv, ver = packet_for(built)
        flows = Investigations(built.engine, _Scripted(inv, ver), saver)
        run_id = flows.create(TENANT, built.claims[case.id], "evaluation")
        t = datetime(2026, 9, 30, 9)
        jobs.enqueue(built.store, TENANT, "investigate", f"run:{run_id}", {"run_id": run_id}, now=t)
        slow = jobs.lease(built.store, "worker-a", 30, now=t)           # leased, then stalls past its lease
        built.engine.clock = lambda: t + timedelta(seconds=60)
        stats = work(built.engine, "worker-b", investigations=flows, lease_seconds=120)
        try:
            jobs.complete(built.store, slow.id, slow.token, now=t + timedelta(seconds=90))
            late = "accepted"
        except jobs.LeaseLost:
            late = "refused"
        status = flows.status(TENANT, run_id)
        proposals = sum(1 for r in status["recommendations"] if r.get("proposal_id"))
        out.append({"scenario": "a worker stalls past its lease, another takes the run over, the first finishes late",
                    "expected": "the run executes once; the late completion is refused",
                    "observed": f"worker b: {stats.get('done', 0)} done; run {status['status']} with {proposals} "
                                f"proposal(s); worker a's completion {late}",
                    "held": stats.get("done") == 1 and status["status"] == "awaiting_approval" and proposals == 1
                    and late == "refused"})
    finally:
        conn.close()
        built.store.engine.dispose()
    return out


# ---------------------------------------------------------------------------------------------- the report

def render(merged: dict, meta: dict, fault_rows: list[dict] | None = None, earlier: list[dict] | None = None) -> str:
    """`earlier`: the runs before this one on the same cases, each {"label", "commit", "summary", "change"} (what
    changed after it), kept in the report so a number that moved says why."""
    s = summarise(merged)
    cats = by_category(merged)
    n = len(merged["cases"])
    lines = ["# The agents against the rules: a holdout comparison", "",
             f"Generated {meta['generated']} at commit `{meta['commit']}` with `{meta['command']}`"
             + (f", from the arms run at commit `{merged['commit']}`" if merged.get("commit") else "") + ".", "",
             f"Cases: {n}, written by hand in `evals/holdout/cases.yaml` (set `{merged['set']}`), independently of "
             "the world generator. Model: " + (f"`{merged['model']}` (fingerprint `{merged['fingerprint']}`)"
                                               if merged.get("model") else "none (the rules alone)") + ".", "",
             "The engine's clause tier is off in this comparison, so the agents are the only model reading the "
             "documents. An agent arm's output on a case is the coordinator's recommendation on the case's target (a "
             "proposed verdict, or a question for a person); where it recommends nothing, the rules' output stands. "
             "A proposal is counted as the output, as if a person approved it: the measure of what the agents would "
             "lead a reviewer to.", "",
             "## By arm", "",
             "| | Rules | One agent | Investigator and verifier |", "|---|---|---|---|"]

    def cell(arm, fn):
        return fn(s[arm]) if arm in s else "-"
    rows = [
        ("Right output", lambda m: f"{m['success']} of {m['cases']}"),
        ("False PASS", lambda m: str(m["false_pass"])),
        ("False FAIL", lambda m: str(m["false_fail"])),
        ("Sent to a person", lambda m: f"{m['person']} of {m['cases']}"),
        ("Citations that held", lambda m: f"{m['citations_valid']} of {m['citations']}" if "citations" in m else "-"),
        ("Tool calls refused", lambda m: f"{m['refused']} of {m['tool_calls']}" if "tool_calls" in m else "-"),
        ("Permission violations", lambda m: str(m["violations"]) if "violations" in m else "-"),
        ("Latency p50 / p95", lambda m: f"{m['p50_seconds']} s / {m['p95_seconds']} s" if "p50_seconds" in m else "-"),
        ("Tokens per case", lambda m: f"{m['tokens_per_case']:,}" if "tokens_per_case" in m else "-"),
        ("Model cost", lambda m: "0 (an open-weight model on the runner's CPU)" if "tokens_per_case" in m else "-"),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | {cell('rules', fn)} | {cell('single', fn)} | {cell('pair', fn)} |")
    stops = {arm: s[arm].get("stops") for arm in ("single", "pair") if arm in s}
    if stops:
        lines += ["", "How the agents stopped: " + "; ".join(
            f"{'one agent' if arm == 'single' else 'the pair'}: " + ", ".join(f"{k} {v}" for k, v in st.items())
            for arm, st in stops.items() if st) + "."]
    lines += ["", "## By kind of case", "", "| Kind | Cases | Rules | One agent | Pair |", "|---|---|---|---|---|"]
    for cat, arms in cats.items():
        k = next(iter(arms.values())).split(" of ")[1]
        lines.append(f"| {cat} | {k} | {arms.get('rules', '-')} | {arms.get('single', '-')} | {arms.get('pair', '-')} |")
    lines += ["", "## Case by case", "", "| Case | Right | Rules | One agent | Pair |", "|---|---|---|---|---|"]
    for c in merged["cases"]:
        def mark(arm, c=c):
            o = c["outputs"].get(arm)
            return "-" if o is None else (f"{o}" if o in c["truth"] else f"**{o}** (wrong)")
        lines.append(f"| {c['case']} | {' or '.join(c['truth'])} | {mark('rules')} | {mark('single')} | "
                     f"{mark('pair')} |")
    if earlier:
        lines += ["", "## Earlier runs on the same cases", "",
                  "| Run | Commit | Rules | One agent | Pair | How the agents stopped |", "|---|---|---|---|---|---|"]
        for e in earlier:
            es = e["summary"]

            def short(arm, es=es):
                m = es.get(arm)
                return "-" if m is None else f"{m['success']} of {m['cases']} right, {m['false_pass']} false PASS"
            how = "; ".join(f"{'one agent' if arm == 'single' else 'the pair'}: " +
                            ", ".join(f"{k} {v}" for k, v in (es.get(arm, {}).get("stops") or {}).items())
                            for arm in ("single", "pair") if arm in es)
            lines.append(f"| {e['label']} | `{e['commit']}` | {short('rules')} | {short('single')} | "
                         f"{short('pair')} | {how} |")
        lines += [""] + [f"After {e['label']}: {e['change']}" for e in earlier if e.get("change")]
    if fault_rows:
        lines += ["", "## Faults forced on purpose (a scripted model: deterministic, no language model)", "",
                  "| Scenario | Expected | Observed | Held |", "|---|---|---|---|"]
        for f in fault_rows:
            lines.append(f"| {f['scenario']} | {f['expected']} | {f['observed']} | {'yes' if f['held'] else '**no**'} |")
    lines += ["", "## What this does and does not show", "",
              f"- {n} cases is a small set: a difference of one or two cases between arms is not evidence of a "
              "difference. The set is for finding where each arm goes wrong, case by case, more than for a rate.",
              "- The cases were written by the author of the system, before any agent ran on them; they are "
              "independent of the generator, not of the author.",
              "- Latency is wall-clock on one GitHub-hosted runner (4 vCPU, no GPU), the model through llama.cpp. A "
              "replay from the recorded calls reproduces every output above, not the timings.",
              "- A proposal is only a proposal: in the service a person approves or rejects it, against the evidence "
              "it was bound to."]
    return "\n".join(lines) + "\n"


__all__ = ["load", "build", "evaluate", "merge", "summarise", "render", "faults", "rules_output", "agent_output"]
