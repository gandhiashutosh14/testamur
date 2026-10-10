"""Shadow trial for a cheaper OCR engine.

A candidate engine runs beside the canonical one on a sample of the pages that need OCR. It is observe-only: its
output never reaches a verdict. For each page both outputs are read into fields and compared with the ground truth,
and the time per page is recorded. A parity gate then decides whether switching to the candidate is allowed; the
switch itself is configuration (`TESTAMUR_OCR_VARIANT`), and extraction caches are keyed by engine, so switching
back is instant and nothing is mixed.

The report carries metrics only, never document text: what a trial on real tenant data could safely log."""
from __future__ import annotations

import random
import time
from collections import Counter
from datetime import date
from pathlib import Path

from .extract import Extractor, RapidOcr, sniff_mime
from .pack import load_pack
from .synth.world import load_world
from .textnorm import name_similarity, squash
from .understand import read_fields

NAME_FIELDS = {"creditor_name", "charge_holder", "account_holder", "company", "registrar", "lender"}
GATE = {"max_accuracy_drop_points": 0.5, "max_fields_lost_share": 0.01, "min_speedup": 1.2}


def _engine_value(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, tuple):
        return str(int(v[0]))
    return str(v)


def _correct(name: str, got: str | None, truth: str) -> bool:
    if got is None:
        return False
    if name in NAME_FIELDS:
        return name_similarity(got, truth) >= 92
    return squash(got) == squash(truth)


def run(world_dir: str | Path, candidate: str, sample: float = 0.4, seed: int = 0) -> dict:
    world = load_world(world_dir)
    pack = load_pack()
    canonical, trial = Extractor(RapidOcr("default")), Extractor(RapidOcr(candidate))
    rng = random.Random(seed)
    pool = [u for u in world.uploads if u.truth.duplicate_of is None and u.truth.format in ("scan", "photo")
            and u.truth.doc_type in pack.doc_types]
    picked = sorted(rng.sample(pool, k=max(1, int(len(pool) * sample))), key=lambda u: u.id)
    c = Counter()
    secs = {"canonical": 0.0, "candidate": 0.0}
    pages = 0
    for u in picked:
        data = (Path(world_dir) / u.file).read_bytes()
        mime = sniff_mime(data)
        t0 = time.perf_counter()
        a = canonical.extract(data, mime)
        t1 = time.perf_counter()
        b = trial.extract(data, mime)
        t2 = time.perf_counter()
        secs["canonical"] += t1 - t0
        secs["candidate"] += t2 - t1
        pages += a.ocr_pages
        spec = pack.doc_types[u.truth.doc_type]
        fa, fb = read_fields(spec, a), read_fields(spec, b)
        for name, truth in u.truth.fields.items():
            if name not in spec.fields or not truth or name in u.truth.ambiguous:
                continue
            ok_a = _correct(name, _engine_value(fa.get(name)), truth)
            ok_b = _correct(name, _engine_value(fb.get(name)), truth)
            c["fields"] += 1
            c["canonical_correct"] += ok_a
            c["candidate_correct"] += ok_b
            c["lost"] += ok_a and not ok_b
            c["gained"] += ok_b and not ok_a
    n = max(1, c["fields"])
    acc_a, acc_b = c["canonical_correct"] / n, c["candidate_correct"] / n
    speedup = secs["canonical"] / max(secs["candidate"], 1e-9)
    checks = {
        "accuracy drop (points)": (round(100 * (acc_a - acc_b), 2), "<=", GATE["max_accuracy_drop_points"]),
        "share of fields lost": (round(c["lost"] / n, 4), "<=", GATE["max_fields_lost_share"]),
        "speed-up": (round(speedup, 2), ">=", GATE["min_speedup"]),
    }
    verdicts = {k: (v <= lim if op == "<=" else v >= lim) for k, (v, op, lim) in checks.items()}
    return {"candidate": candidate, "documents": len(picked), "pages": pages, "fields": c["fields"],
            "canonical_accuracy": round(acc_a, 4), "candidate_accuracy": round(acc_b, 4),
            "fields_lost": c["lost"], "fields_gained": c["gained"],
            "canonical_seconds_per_page": round(secs["canonical"] / max(1, pages), 3),
            "candidate_seconds_per_page": round(secs["candidate"] / max(1, pages), 3),
            "gate": {k: {"value": v, "op": op, "limit": lim, "ok": verdicts[k]} for k, (v, op, lim) in checks.items()},
            "cutover_allowed": all(verdicts.values()), "world_seed": world.seed, "sample": sample}


def render(results: list[dict], meta: dict) -> str:
    out = ["# Shadow OCR trial", "",
           f"Commit `{meta['commit']}`, command `{meta['command']}`. Candidates ran beside the canonical engine "
           f"(`rapidocr`, PP-OCRv6 small) on a {int(100 * results[0]['sample'])}% sample of the scanned and photographed "
           f"documents of world seed {results[0]['world_seed']}, observe-only. Field values read from each engine's text "
           "are compared with the ground truth. Only metrics are recorded, never text.", "",
           "| Candidate | Documents | Pages | Fields | Canonical accuracy | Candidate accuracy | Lost | Gained | "
           "s/page canonical | s/page candidate | Cutover allowed |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        out.append(f"| {r['candidate']} | {r['documents']} | {r['pages']} | {r['fields']} | "
                   f"{100 * r['canonical_accuracy']:.1f}% | {100 * r['candidate_accuracy']:.1f}% | {r['fields_lost']} | "
                   f"{r['fields_gained']} | {r['canonical_seconds_per_page']} | {r['candidate_seconds_per_page']} | "
                   f"{'**yes**' if r['cutover_allowed'] else 'no'} |")
    out += ["", "Gate: the candidate may lose at most "
            f"{GATE['max_accuracy_drop_points']} points of field accuracy and {100 * GATE['max_fields_lost_share']:.0f}% "
            f"of fields the canonical engine reads correctly, and must be at least {GATE['min_speedup']}x faster.", ""]
    for r in results:
        failed = [k for k, g in r["gate"].items() if not g["ok"]]
        out.append(f"- **{r['candidate']}**: " + ("passes every condition." if not failed else
                   "fails on " + ", ".join(f"{k} ({r['gate'][k]['value']} {r['gate'][k]['op']} {r['gate'][k]['limit']})"
                                           for k in failed) + "."))
    out += ["", "Switching is configuration (`TESTAMUR_OCR_VARIANT=<candidate>`); extraction caches are keyed by "
            "engine, so a switch back is instant. Timings are this machine's.", ""]
    return "\n".join(out)
