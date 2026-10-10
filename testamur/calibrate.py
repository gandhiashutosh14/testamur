"""Choose the name-matching threshold with a guarantee instead of by eye.

How low can the bar for attaching a document to a creditor by name go before more than alpha of documents are
attached to the wrong creditor? Learn then Test (Angelopoulos, Bates, Candes, Jordan and Lei; Annals of Applied
Statistics, 2025) answers with a finite-sample guarantee. For each candidate threshold, strictest first, test the
hypothesis "the wrong-attachment rate exceeds alpha" with an exact binomial p-value on labelled documents, and keep
lowering the bar while the test rejects at level delta. With probability at least 1 - delta over the calibration
sample, the wrong-attachment rate at the chosen threshold is at most alpha, for documents drawn like the calibration
ones (and only for those: a new kind of document is a new distribution).

Labelled documents come from worlds with ground truth here. In production they come from people's decisions about
matches: documents assigned or rejected from the queue, and audits of documents the engine attached by itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from .accuracy import binom_cdf
from .match import ACCEPT, EXACT, MARGIN, NAME_FIELD, scope
from .store import Creditor, Document, Store, UploadRow
from .synth.world import World
from .textnorm import name_similarity

GRID = [round(100 - 0.5 * i, 1) for i in range(61)]          # 100.0 down to 70.0, strictest first


@dataclass
class Labelled:
    best: float           # similarity of the best-scoring creditor in scope
    second: float         # ... of the runner-up
    correct: bool         # the best-scoring creditor is the document's true owner


def accepted(r: Labelled, threshold: float) -> bool:
    """The matcher's name rule at a given acceptance threshold (margin and exact-name rules unchanged)."""
    return (r.best >= threshold and r.best - r.second >= MARGIN) or (r.best >= EXACT > r.second)


def labelled_names(world: World, store: Store) -> list[Labelled]:
    """One row per document the engine matched (or tried to match) by name rather than by an identifier: the scores
    the matcher saw for the name it read, and whether its best candidate is the true owner. A document whose owner is
    not in scope (misfiled, another tenant's, a stranger's) is never correctly accepted."""
    uploads = {u.id: u for u in world.uploads}
    rows: list[Labelled] = []
    with store.system() as s:
        pool: dict[int, list[Creditor]] = {}
        for sup in s.scalars(select(Creditor)):
            pool.setdefault(sup.tenant_id, []).append(sup)
        for d in s.scalars(select(Document).order_by(Document.id)):
            if d.doc_type is None or d.match_method == "identifier":
                continue
            name = ((d.fields or {}).get(NAME_FIELD[d.doc_type]) or {}).get("value")
            if not isinstance(name, str):
                continue
            up = s.scalars(select(UploadRow).where(UploadRow.tenant_id == d.tenant_id, UploadRow.sha256 == d.sha256)
                           .order_by(UploadRow.id)).first()
            truth = uploads[int(up.external_ref.split(":")[1])].truth
            in_scope = scope(s, pool[d.tenant_id], up.matter_id)
            scored = sorted(((name_similarity(name, x.legal_name), x.id) for x in pool[d.tenant_id] if x.id in in_scope),
                            reverse=True)
            (best, best_id), (second, _) = (scored + [(0.0, None), (0.0, None)])[:2]
            rows.append(Labelled(best, second, best_id is not None and best_id == truth.owner_creditor_id))
    return rows


def learn_then_test(rows: list[Labelled], alpha: float, delta: float = 0.05,
                    grid: list[float] | None = None) -> dict:
    """Fixed-sequence testing down the grid. Returns the lowest threshold certified at (alpha, delta), or None if
    even the strictest is not (too few labelled documents: with no errors at all, about log(delta)/log(1-alpha)
    are needed)."""
    n = len(rows)
    chosen, table = None, []
    for threshold in grid or GRID:
        took = [r for r in rows if accepted(r, threshold)]
        wrong = sum(not r.correct for r in took)
        p = binom_cdf(wrong, n, alpha)         # P(Binomial(n, alpha) <= wrong): small means the rate is below alpha
        table.append({"threshold": threshold, "accepted": len(took), "wrong": wrong, "p_value": p, "safe": p <= delta})
        if p > delta:
            break
        chosen = threshold
    return {"alpha": alpha, "delta": delta, "n": n, "threshold": chosen, "table": table}


def evaluate(rows: list[Labelled], threshold: float) -> dict:
    took = [r for r in rows if accepted(r, threshold)]
    return {"threshold": threshold, "n": len(rows), "accepted": len(took), "wrong": sum(not r.correct for r in took),
            "accepted_share": round(len(took) / max(1, len(rows)), 4)}


def render(cal: list[dict], n_rows: int, seeds: str, validation: dict | None, meta: dict) -> str:
    out = ["# Calibrating the name-matching threshold", "",
           f"Commit `{meta['commit']}`, command `{meta['command']}`.", "",
           f"Calibration: {n_rows} documents matched by name in born-digital worlds (seeds {seeds}), labelled by the "
           f"generator's ground truth. Method: Learn then Test, fixed-sequence over thresholds from 100 down in steps "
           f"of 0.5, exact binomial p-values. The margin rule ({MARGIN} points over the runner-up) and the exact-name "
           f"rule are unchanged. Today's hand-set threshold is {ACCEPT}.", "",
           "| alpha (allowed wrong-attachment rate) | delta | Certified threshold | Accepted at it | Wrong at it |",
           "|---|---|---|---|---|"]
    for c in cal:
        last = next((t for t in reversed(c["table"]) if t["safe"]), None)
        out.append(f"| {c['alpha']:.0%} | {c['delta']:.0%} | {c['threshold'] if c['threshold'] is not None else 'none'} "
                   f"| {last['accepted'] if last else '-'} | {last['wrong'] if last else '-'} |")
    if validation:
        out += ["", f"Validation on a different world with scanned and photographed documents "
                f"({validation['label']}, {validation['rows']} documents matched by name):", "",
                "| Threshold | Accepted | Share accepted | Wrong attachments |", "|---|---|---|---|"]
        for v in validation["results"]:
            out.append(f"| {v['threshold']}{' (today)' if v['threshold'] == ACCEPT else ''} | {v['accepted']} | "
                       f"{v['accepted_share']:.1%} | {v['wrong']} |")
    out += ["", "## What this does and does not show", "",
            "- The guarantee holds for documents like the calibration ones. Scans and photographs are a different "
            "distribution (OCR changes names), which is why the threshold is checked on a scanned world as well.",
            "- The generator's near-twin names are one model of how names collide; real creditor registers will "
            "collide differently. In production the labels come from people's decisions on matches.",
            "- Accepting more by name only automates more if nothing else holds the document back: claims, misfiled "
            "uploads and identifiers still apply.", ""]
    return "\n".join(out)
