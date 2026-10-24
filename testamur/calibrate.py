"""Choose the name-matching threshold with a guarantee instead of by eye.

How low can the bar for attaching a document to a creditor by name go before more than alpha of the documents it
attaches go to the wrong creditor? Learn then Test (Angelopoulos, Bates, Candes, Jordan and Lei; Annals of Applied
Statistics, 2025) answers with a finite-sample bound. For each candidate threshold, strictest first, test the
hypothesis "the rate exceeds alpha" with an exact binomial p-value, and keep lowering the bar while the test rejects at
level delta (fixed-sequence testing, so the family of tests holds at delta).

Which rate matters. The rate a person relying on an automatic attachment cares about is the share of ACCEPTED
documents attached to the wrong creditor (selective risk): tested conditionally on the number accepted at the
threshold, Binomial(accepted, alpha). An earlier version divided by every labelled document instead (the unconditional
rate of "accepted and wrong"), which abstentions dilute: 10 wrong attachments among 10 accepted and 9,990 abstentions
would have certified 1%. Both are reported; the threshold is certified on the selective one.

The bound holds with probability at least 1 - delta over the calibration sample, for documents drawn like the
calibration ones (exchangeable with them: a new kind of document, or a new OCR engine, is a new distribution).

Labelled documents come from worlds with ground truth here. In production they come from people's decisions about
matches: documents assigned or rejected from the queue, and audits of documents the engine attached by itself.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from .accuracy import binom_cdf
from .match import ACCEPT, EXACT, MARGIN, scope
from .pack import load_pack
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
    pack = load_pack()
    rows: list[Labelled] = []
    with store.system() as s:
        pool: dict[int, list[Creditor]] = {}
        for sup in s.scalars(select(Creditor)):
            pool.setdefault(sup.tenant_id, []).append(sup)
        for d in s.scalars(select(Document).order_by(Document.id)):
            if d.doc_type is None or d.match_method == "identifier":
                continue
            name = ((d.fields or {}).get(pack.doc_types[d.doc_type].name_field) or {}).get("value")
            if not isinstance(name, str):
                continue
            up = s.scalars(select(UploadRow).where(UploadRow.tenant_id == d.tenant_id, UploadRow.sha256 == d.sha256,
                                                   UploadRow.matter_id == d.matter_id)
                           .order_by(UploadRow.id)).first()
            truth = uploads[int(up.external_ref.split(":")[1])].truth
            in_scope = scope(s, pool[d.tenant_id], up.matter_id)
            scored = sorted(((name_similarity(name, x.legal_name), x.id) for x in pool[d.tenant_id] if x.id in in_scope),
                            reverse=True)
            (best, best_id), (second, _) = (scored + [(0.0, None), (0.0, None)])[:2]
            rows.append(Labelled(best, second, best_id is not None and best_id == truth.owner_creditor_id))
    return rows


def learn_then_test(rows: list[Labelled], alpha: float, delta: float = 0.05,
                    grid: list[float] | None = None, risk: str = "selective") -> dict:
    """Fixed-sequence testing down the grid. Returns the lowest threshold certified at (alpha, delta), or None if
    even the strictest is not (too few documents: with no errors at all, about log(delta)/log(1-alpha) are needed,
    accepted ones for the selective risk). `risk`: "selective" (wrong among accepted, the default) or "unconditional"
    (accepted and wrong, among all labelled; reported for comparison, never the default)."""
    if risk not in ("selective", "unconditional"):
        raise ValueError("risk is selective or unconditional")
    n = len(rows)
    chosen, table = None, []
    for threshold in grid or GRID:
        took = [r for r in rows if accepted(r, threshold)]
        wrong = sum(not r.correct for r in took)
        trials = len(took) if risk == "selective" else n
        if not trials:
            # nothing accepted (the strictest end of the grid): nothing attached, nothing to test or certify
            table.append({"threshold": threshold, "accepted": 0, "wrong": 0, "selective_risk": None,
                          "unconditional_risk": 0.0 if n else None, "p_value": None, "safe": True})
            continue
        # P(Binomial(trials, alpha) <= wrong): small means the rate is below alpha
        p = binom_cdf(wrong, trials, alpha)
        table.append({"threshold": threshold, "accepted": len(took), "wrong": wrong,
                      "selective_risk": round(wrong / len(took), 4) if took else None,
                      "unconditional_risk": round(wrong / n, 4) if n else None, "p_value": p, "safe": p <= delta})
        if p > delta:
            break
        chosen = threshold
    return {"alpha": alpha, "delta": delta, "n": n, "risk": risk, "threshold": chosen, "table": table}


def evaluate(rows: list[Labelled], threshold: float) -> dict:
    took = [r for r in rows if accepted(r, threshold)]
    wrong = sum(not r.correct for r in took)
    return {"threshold": threshold, "n": len(rows), "accepted": len(took), "wrong": wrong,
            "accepted_share": round(len(took) / max(1, len(rows)), 4),
            "selective_risk": round(wrong / len(took), 4) if took else None}


def render(cal: list[dict], n_rows: int, seeds: str, validation: dict | None, meta: dict) -> str:
    out = ["# Calibrating the name-matching threshold", "",
           f"Commit `{meta['commit']}`, command `{meta['command']}`.", "",
           f"Calibration: {n_rows} documents matched by name in born-digital worlds (seeds {seeds}), labelled by the "
           f"generator's ground truth. Method: Learn then Test, fixed-sequence over thresholds from 100 down in steps "
           f"of 0.5, exact binomial p-values. The rate certified is the share of accepted documents attached to the "
           f"wrong creditor (selective risk, tested on the number accepted); the share of all labelled documents that "
           f"were accepted and wrong is shown beside it. The margin rule ({MARGIN} points over the runner-up) and the "
           f"exact-name rule are unchanged. Today's hand-set threshold is {ACCEPT}.", "",
           "| alpha (allowed rate of wrong attachments among accepted) | delta | Certified threshold | Accepted at it | "
           "Wrong at it | Wrong among accepted | Accepted and wrong, among all |", "|---|---|---|---|---|---|---|"]
    for c in cal:
        last = next((t for t in reversed(c["table"]) if t["safe"]), None)
        sel = f"{last['selective_risk']:.2%}" if last and last["selective_risk"] is not None else "-"
        unc = f"{last['unconditional_risk']:.2%}" if last and last["unconditional_risk"] is not None else "-"
        out.append(f"| {c['alpha']:.0%} | {c['delta']:.0%} | {c['threshold'] if c['threshold'] is not None else 'none'} "
                   f"| {last['accepted'] if last else '-'} | {last['wrong'] if last else '-'} | {sel} | {unc} |")
    if validation:
        out += ["", f"Validation on a different world with scanned and photographed documents "
                f"({validation['label']}, {validation['rows']} documents matched by name):", "",
                "| Threshold | Accepted | Share accepted | Wrong attachments | Wrong among accepted |",
                "|---|---|---|---|---|"]
        for v in validation["results"]:
            sel = f"{v['selective_risk']:.2%}" if v.get("selective_risk") is not None else "-"
            out.append(f"| {v['threshold']}{' (today)' if v['threshold'] == ACCEPT else ''} | {v['accepted']} | "
                       f"{v['accepted_share']:.1%} | {v['wrong']} | {sel} |")
    out += ["", "## What this does and does not show", "",
            "- The guarantee holds for documents like the calibration ones. Scans and photographs are a different "
            "distribution (OCR changes names), which is why the threshold is checked on a scanned world as well.",
            "- The generator's near-twin names are one model of how names collide; real creditor registers will "
            "collide differently. In production the labels come from people's decisions on matches.",
            "- Accepting more by name only automates more if nothing else holds the document back: claims, misfiled "
            "uploads and identifiers still apply.", ""]
    return "\n".join(out)
