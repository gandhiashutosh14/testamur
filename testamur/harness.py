"""The offline harness: run the real engine over a generated world, compare everything with ground truth, enforce
the gates, and write a report stamped with the commit and the command that produced it.

The numbers that matter most are the ones in the dangerous direction: a status that vouches for a supplier when the
truth says otherwise, a check that says PASS when the truth says FAIL, and a document attached to the wrong
supplier. The gates hold those at zero. Deferrals (NEEDS_REVIEW) are reported as the price of that safety."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select

from .engine import Engine
from .model import VOUCHING, Severity, Status, Tier, Verdict
from .modeltier import ModelClient
from .pack import load_pack
from .store import (
    Attestation,
    BusinessUnit,
    Document,
    Engagement,
    ModelCall,
    RequirementState,
    Store,
    Supplier,
    Tenant,
    UsageRow,
    VerdictRow,
)
from .synth.truth import truth_statuses
from .synth.world import World, load_world


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + margin) / (1 + z * z / n))


def git_stamp(cwd: Path) -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd, capture_output=True,
                               text=True).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


def load_registry(store: Store, world: World) -> None:
    with store.system() as s:
        s.add_all([Tenant(id=t.id, name=t.name) for t in world.tenants])
        s.add_all([BusinessUnit(id=b.id, tenant_id=b.tenant_id, name=b.name) for b in world.business_units])
        s.add_all([Supplier(id=x.id, tenant_id=x.tenant_id, legal_name=x.legal_name, address=x.address,
                            company_number=x.company_number, vat_number=x.vat_number, lei=x.lei, iban=x.iban,
                            parent_id=x.parent_id) for x in world.suppliers])
        s.add_all([Engagement(id=e.id, tenant_id=e.tenant_id, business_unit_id=e.business_unit_id,
                              supplier_id=e.supplier_id, tier=e.tier, engagement_type=e.engagement_type,
                              service_category=e.service_category, start_date=e.start_date, end_date=e.end_date)
                   for e in world.engagements])
        s.add_all([Attestation(tenant_id=a.tenant_id, supplier_id=a.supplier_id, requirement_id=a.requirement_id,
                               verified_by=a.verified_by, verified_on=a.verified_on, note=a.note)
                   for a in world.attestations])


@dataclass
class Report:
    meta: dict[str, Any]
    metrics: dict[str, Any]
    gates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(g["ok"] for g in self.gates)


def _usage(store: Store) -> dict[str, float]:
    with store.system() as s:
        ocr = s.scalar(select(func.coalesce(func.sum(UsageRow.units), 0)).where(UsageRow.provider == "ocr")) or 0
        calls = s.scalar(select(func.count()).select_from(ModelCall)) or 0
        tokens = s.scalar(select(func.coalesce(func.sum(ModelCall.prompt_tokens + ModelCall.completion_tokens), 0))) or 0
        secs = s.scalar(select(func.coalesce(func.sum(ModelCall.seconds), 0.0))) or 0.0
    return {"ocr_pages": float(ocr), "model_calls": float(calls), "model_tokens": float(tokens),
            "model_seconds": float(secs)}


def process_world(world_dir: str | Path, db_path: str | Path, model: ModelClient | None,
                  pack=None) -> tuple[World, Store, Engine, float]:
    """A fresh database with the world's registry, every upload ingested and processed, every status computed."""
    world = load_world(world_dir)
    pack = pack or load_pack()
    if world.pack != pack.digest:
        raise SystemExit(f"world was generated with pack {world.pack}, engine has {pack.digest}: regenerate it")
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    blob_root = db_path.parent / (db_path.stem + "_blobs")
    shutil.rmtree(blob_root, ignore_errors=True)
    store = Store(f"sqlite:///{db_path}")
    store.setting("as_of", world.as_of.isoformat())          # so later commands judge at the same date
    store.setting("blob_root", str(blob_root))
    load_registry(store, world)
    engine = Engine(store, pack, world.as_of, blob_root, model=model)
    t0 = time.perf_counter()
    for u in world.uploads:
        data = (Path(world_dir) / u.file).read_bytes()
        uid = engine.ingest(u.tenant_id, u.business_unit_id, u.filename, data, u.channel, u.uploaded_at,
                            u.claimed_supplier_id, external_ref=f"world:{u.id}")
        engine.process(u.tenant_id, uid)
    for t in world.tenants:
        engine.refresh(t.id)
    return world, store, engine, time.perf_counter() - t0


def run(world_dir: str | Path, db_path: str | Path, model: ModelClient | None, *, reindex_check: bool = True,
        command: str = "") -> Report:
    world, store, engine, first_pass = process_world(world_dir, db_path, model)
    pack = engine.pack
    usage1 = _usage(store)

    reindex = {}
    if reindex_check:
        t1 = time.perf_counter()
        for t in world.tenants:
            engine.reindex(t.id)
            engine.refresh(t.id)
        usage2 = _usage(store)
        reindex = {"seconds": round(time.perf_counter() - t1, 1),
                   "new_ocr_pages": usage2["ocr_pages"] - usage1["ocr_pages"],
                   "new_model_calls": usage2["model_calls"] - usage1["model_calls"]}

    metrics = measure(world, Path(world_dir), store, pack)
    metrics["cost"] = {**usage1, "first_pass_seconds": round(first_pass, 1), "reindex": reindex,
                       "documents": metrics["classification"]["documents"]}
    meta = {"generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_stamp(Path.cwd()),
            "command": command or " ".join(sys.argv), "world_seed": world.seed, "world_size": world.size,
            "as_of": world.as_of.isoformat(), "pack": pack.digest, "engine": engine.version,
            "model": getattr(model, "model_id", None) or "none", "model_mode": _model_mode(model),
            "ocr": engine.extractor.version,
            "uploads": len(world.uploads), "python": sys.version.split()[0]}
    return Report(meta, metrics)


def _model_mode(model) -> str:
    """live: answers computed now; replay: answers recorded earlier with this model, replayed exactly."""
    if model is None:
        return "none"
    return "replay" if getattr(model, "inner", "live") is None else "live"


def measure(world: World, world_dir: Path, store: Store, pack) -> dict[str, Any]:
    by_sha: dict[str, Document] = {}
    with store.system() as s:
        for d in s.scalars(select(Document)).all():
            by_sha[f"{d.tenant_id}:{d.sha256}"] = d
        states = {(r.engagement_id, r.requirement_id): r for r in s.scalars(select(RequirementState)).all()}
        verdicts = defaultdict(dict)
        for v in s.scalars(select(VerdictRow)).all():
            verdicts[(v.engagement_id, v.requirement_id, v.document_id)][v.check_id] = v
    up_doc: dict[int, Document] = {}
    for u in world.uploads:
        sha = sha256((world_dir / u.file).read_bytes()).hexdigest()
        up_doc[u.id] = by_sha[f"{u.tenant_id}:{sha}"]

    originals = [u for u in world.uploads if u.truth.duplicate_of is None]
    # classification
    cls = Counter()
    for u in originals:
        truth = u.truth.doc_type if u.truth.doc_type != "unknown" else None
        cls[(truth or "unknown", up_doc[u.id].doc_type or "unknown")] += 1
    correct_cls = sum(n for (t, p), n in cls.items() if t == p)
    # matching
    m = Counter()
    wrong_matches = []
    for u in originals:
        d = up_doc[u.id]
        if u.truth.doc_type == "unknown" or u.truth.variant == "wrong_tenant" and d.supplier_id is None:
            if u.truth.variant == "wrong_tenant":
                m["abstained_as_expected"] += 1
            continue
        if d.doc_type != u.truth.doc_type:
            m["not_classified"] += 1
            continue
        owner = u.truth.owner_supplier_id
        if d.supplier_id is None:
            m["abstained_as_expected" if u.truth.expect == "abstain" else "abstained_deferral"] += 1
        elif d.supplier_id == owner:
            m["matched_correctly"] += 1
        else:
            m["wrong_match"] += 1
            wrong_matches.append({"upload": u.id, "variant": u.truth.variant, "truth_owner": owner,
                                  "engine": d.supplier_id, "reason": (d.match_detail or {}).get("reason")})
    # checks (only where engine and truth judge the same document)
    truth = truth_statuses(world, pack)
    confusion: dict[str, Counter] = defaultdict(Counter)
    false_pass_examples = []
    for (eid, rid), t in truth.items():
        if t["upload_id"] is None:
            continue
        d = up_doc[t["upload_id"]]
        state = states.get((eid, rid))
        if state is None or state.document_id != d.id:
            continue
        rows = verdicts.get((eid, rid, d.id), {})
        for cid, tv in t["checks"].items():
            row = rows.get(cid)
            ev = row.verdict if row else "MISSING"
            confusion[cid][(str(tv), ev)] += 1
            if ev == Verdict.PASS and tv != Verdict.PASS and len(false_pass_examples) < 20:
                false_pass_examples.append({"engagement": eid, "requirement": rid, "check": cid, "truth": str(tv),
                                            "reason": row.reason, "upload": t["upload_id"]})
    per_check = {}
    for cid, c in sorted(confusion.items()):
        spec = pack.checks[cid]
        truth_fail = sum(n for (tv, _), n in c.items() if tv in ("FAIL", "ABSTAIN"))
        fp = sum(n for (tv, ev), n in c.items() if tv in ("FAIL", "ABSTAIN") and ev == "PASS")
        total = sum(c.values())
        agree = sum(n for (tv, ev), n in c.items() if tv == ev)
        abstain = sum(n for (tv, ev), n in c.items() if ev == "ABSTAIN" and tv != "ABSTAIN")
        per_check[cid] = {"tier": spec.tier.value, "severity": spec.severity.value, "n": total, "agree": agree,
                          "false_pass": fp, "truth_negative": truth_fail, "abstained": abstain,
                          "false_fail": sum(n for (tv, ev), n in c.items() if tv == "PASS" and ev == "FAIL"),
                          "false_pass_upper95": round(wilson_upper(fp, truth_fail), 3) if truth_fail else None}
    by_tier = defaultdict(Counter)
    for r in per_check.values():
        for k in ("n", "agree", "false_pass", "truth_negative", "abstained", "false_fail"):
            by_tier[r["tier"]][k] += r[k]
    # statuses
    status_conf = Counter()
    outcome = Counter()
    false_assurance = []
    for key, t in truth.items():
        ts = t["status"]
        es = Status(states[key].status) if key in states else None
        status_conf[(ts.value, es.value if es else "NONE")] += 1
        if es == ts:
            outcome["exact"] += 1
        elif es == Status.NEEDS_REVIEW:
            outcome["deferred_to_review"] += 1
        elif ts == Status.SATISFIED and es == Status.SATISFIED_WITH_FINDINGS and not any(
                v.verdict == Verdict.FAIL for v in verdicts.get((*key, states[key].document_id), {}).values()):
            outcome["abstention_as_finding"] += 1          # vouching kept; a non-critical "not sure" asks a person
        elif es in VOUCHING and ts not in VOUCHING:
            outcome["false_assurance"] += 1
            false_assurance.append({"engagement": key[0], "requirement": key[1], "truth": ts.value,
                                    "engine": es.value})
        elif es not in VOUCHING and ts in VOUCHING:
            outcome["false_alarm"] += 1
        else:
            outcome["other_mismatch"] += 1
    n_status = sum(outcome.values())
    # security
    injected = [u for u in originals if u.truth.injection]
    detected = sum(1 for u in injected if up_doc[u.id].hostile)
    hostile_model_pass = 0
    for (_eid, _rid, did), rows in verdicts.items():
        d = next((x for x in by_sha.values() if x.id == did), None)
        if d is not None and d.hostile:
            hostile_model_pass += sum(1 for r in rows.values() if r.tier == Tier.MODEL and r.verdict == Verdict.PASS)
    return {
        "classification": {"documents": len(originals), "correct": correct_cls,
                           "accuracy": round(correct_cls / max(1, len(originals)), 4),
                           "confusion": {f"{t} -> {p}": n for (t, p), n in sorted(cls.items())}},
        "matching": {**dict(m), "wrong_match_examples": wrong_matches},
        "checks": {"per_check": per_check, "by_tier": {k: dict(v) for k, v in by_tier.items()},
                   "false_pass_examples": false_pass_examples,
                   "critical_false_pass": sum(r["false_pass"] for r in per_check.values()
                                              if r["severity"] == Severity.CRITICAL.value)},
        "status": {"requirement_instances": n_status, **dict(outcome),
                   "exact_rate": round(outcome["exact"] / max(1, n_status), 4),
                   "deferral_rate": round(outcome["deferred_to_review"] / max(1, n_status), 4),
                   "confusion": {f"{t} -> {e}": n for (t, e), n in sorted(status_conf.items())},
                   "false_assurance_examples": false_assurance},
        "security": {"injected_documents": len(injected), "detected": detected,
                     "model_pass_on_hostile_documents": hostile_model_pass},
    }


def apply_gates(report: Report, gates_file: str | Path) -> None:
    g = yaml.safe_load(Path(gates_file).read_text(encoding="utf-8"))
    m = report.metrics
    injected = m["security"]["injected_documents"]
    rows = [
        ("status false assurance", m["status"].get("false_assurance", 0), "<=", g["status_false_assurance_max"]),
        ("critical check false passes", m["checks"]["critical_false_pass"], "<=", g["critical_check_false_pass_max"]),
        ("documents matched to the wrong supplier", m["matching"].get("wrong_match", 0), "<=", g["wrong_match_max"]),
        ("classification accuracy", m["classification"]["accuracy"], ">=", g["classification_accuracy_min"]),
        ("status deferral rate", m["status"]["deferral_rate"], "<=", g["deferral_rate_max"]),
        ("injected documents detected", (m["security"]["detected"] / injected) if injected else 1.0, ">=",
         g["injection_detection_min"]),
        ("model PASS on hostile documents", m["security"]["model_pass_on_hostile_documents"], "<=", 0),
    ]
    if m["cost"].get("reindex"):
        rows += [("OCR pages paid again on re-index", m["cost"]["reindex"]["new_ocr_pages"], "<=",
                  g["reindex_new_ocr_pages_max"]),
                 ("model calls paid again on re-index", m["cost"]["reindex"]["new_model_calls"], "<=",
                  g["reindex_new_model_calls_max"])]
    report.gates = [{"gate": name, "value": val, "op": op, "limit": lim,
                     "ok": (val <= lim) if op == "<=" else (val >= lim)} for name, val, op, lim in rows]


def write(report: Report, md_path: str | Path) -> None:
    md_path = Path(md_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.with_suffix(".json").write_text(json.dumps({"meta": report.meta, "metrics": report.metrics,
                                                        "gates": report.gates}, indent=2, default=str),
                                            encoding="utf-8")
    md_path.write_text(render(report), encoding="utf-8")


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def render(r: Report) -> str:
    m, meta = r.metrics, r.meta
    st, ck, mt, cl, sec, cost = m["status"], m["checks"], m["matching"], m["classification"], m["security"], m["cost"]
    out = [f"# Harness report: {meta['world_size']} world, seed {meta['world_seed']}", "",
           f"**{'PASSED' if r.passed else 'FAILED'}**: {sum(g['ok'] for g in r.gates)} of {len(r.gates)} gates. "
           f"Generated {meta['generated']} at commit `{meta['commit']}` with `{meta['command']}`.", "",
           f"Engine `{meta['engine']}`, OCR `{meta['ocr']}`, model `{meta['model']}`"
           f"{' (answers replayed from a recording)' if meta.get('model_mode') == 'replay' else ''}, as-of date {meta['as_of']}, "
           f"Python {meta['python']}. {meta['uploads']} uploads, {cl['documents']} distinct documents.", "",
           "## Gates", "", "| Gate | Value | Limit | Result |", "|---|---|---|---|"]
    for g in r.gates:
        val = g["value"] if isinstance(g["value"], int) else round(float(g["value"]), 4)
        out.append(f"| {g['gate']} | {val} | {g['op']} {g['limit']} | {'pass' if g['ok'] else '**FAIL**'} |")
    out += ["", "## Requirement status", "",
            f"{st['requirement_instances']} engagement-requirement pairs. Exact: {st.get('exact', 0)} "
            f"({_pct(st['exact_rate'])}). Deferred to review: {st.get('deferred_to_review', 0)} "
            f"({_pct(st['deferral_rate'])}). Vouching kept, with a non-critical abstention shown as a finding: "
            f"{st.get('abstention_as_finding', 0)}. False assurance (vouching when the truth is otherwise): "
            f"{st.get('false_assurance', 0)}. False alarm: {st.get('false_alarm', 0)}. Other mismatch: "
            f"{st.get('other_mismatch', 0)}.", "", "| Truth -> engine | Count |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in st["confusion"].items()]
    out += ["", "## Checks", "", "By tier (only where the engine and the truth judged the same document):", "",
            "| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |",
            "|---|---|---|---|---|---|---|"]
    for tier in ("metadata", "field", "model"):
        t = ck["by_tier"].get(tier)
        if t:
            out.append(f"| {tier} | {t['n']} | {t['agree']} | {t['false_pass']} | {t['truth_negative']} | "
                       f"{t['abstained']} | {t['false_fail']} |")
    out += ["", "| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |",
            "|---|---|---|---|---|---|---|---|---|"]
    for cid, c in ck["per_check"].items():
        ub = "n/a" if c["false_pass_upper95"] is None else _pct(c["false_pass_upper95"])
        out.append(f"| {cid} | {c['tier']} | {c['severity']} | {c['n']} | {c['agree']} | {c['false_pass']} | {ub} | "
                   f"{c['abstained']} | {c['false_fail']} |")
    out += ["", "## Classification and matching", "",
            f"Classification accuracy {_pct(cl['accuracy'])} ({cl['correct']} of {cl['documents']}).", "",
            "| Matching outcome | Documents |", "|---|---|"]
    out += [f"| {k.replace('_', ' ')} | {v} |" for k, v in mt.items() if k != "wrong_match_examples"]
    calls = (f"{int(cost['model_calls'])} model answers replayed from a recording (recorded cost "
             f"{int(cost['model_tokens'])} tokens; no model ran)" if meta.get("model_mode") == "replay" else
             f"{int(cost['model_calls'])} model calls ({int(cost['model_tokens'])} tokens, "
             f"{cost['model_seconds']:.0f} s of model time)")
    out += ["", "## Cost and re-index", "",
            f"First pass: {cost['first_pass_seconds']} s, {int(cost['ocr_pages'])} OCR pages, {calls} for "
            f"{cost['documents']} documents."]
    if cost.get("reindex"):
        ri = cost["reindex"]
        out.append(f"Re-index of every document: {ri['seconds']} s, {int(ri['new_ocr_pages'])} new OCR pages, "
                   f"{int(ri['new_model_calls'])} new model calls (everything answered from the caches).")
    out += ["", "## Security", "",
            f"{sec['detected']} of {sec['injected_documents']} documents carrying text aimed at automated readers were "
            f"flagged; model checks returned PASS on such documents {sec['model_pass_on_hostile_documents']} times.", "",
            "## What this does not show", "",
            "- The world is synthetic: fictional suppliers and documents rendered from templates, then degraded. It is "
            "adversarial by design, but real evidence is messier in ways a generator does not anticipate.",
            "- Model-tier numbers come from synthetic contract clauses, not from real contracts.",
            "- Timings are what this machine measured, not a benchmark.", ""]
    return "\n".join(out)
