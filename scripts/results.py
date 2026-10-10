"""Write the README's results section from the committed reports, so no number in it is typed by hand.

Usage: python scripts/results.py          rewrites the block between the RESULTS markers in README.md
A test (tests/test_readme.py) fails if the README and the reports disagree.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
START, END = "<!-- RESULTS -->", "<!-- /RESULTS -->"


def _load(name: str) -> dict | None:
    p = REPORTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _pct(x: float, digits: int = 1) -> str:
    return f"{100 * x:.{digits}f}%"


def _commit(r: dict) -> str:
    return (r.get("meta") or {}).get("commit", "?")


def _world(h: dict | None) -> str:
    if not h:
        return ""
    from testamur.pack import load_pack
    from testamur.synth.world import SIZES
    meta, st = h["meta"], h["metrics"]["status"]
    claims = st["requirement_instances"] // len(load_pack().requirements)
    return (f" The main world is seed {meta['world_seed']}: {SIZES[meta['world_size']]['tenants']} insolvency firms, "
            f"{claims} claims, {meta['uploads']} uploads.")


def render() -> str:
    h = _load("harness-full-seed11.json")
    out = ["## Results", "",
           "Measured on generated, adversarial insolvency worlds with ground truth: fictional lenders, trustees and "
           "corporate debtors; scans and phone photos; near-twin names; decoy pages; authorities that contradict "
           "themselves; claims above the books; defaults outside limitation; changed bank accounts; text aimed at "
           "automated readers. Each number below is written from a committed report by "
           "[`scripts/results.py`](scripts/results.py) (a test keeps the two in step), and each report states the "
           "commit and the command that produced it." + _world(h)]
    if h:
        m = h["metrics"]
        st, ck, cl, mt, sec, cost = (m["status"], m["checks"]["by_tier"], m["classification"], m["matching"],
                                     m["security"], m["cost"])
        det = ck["metadata"]["agree"] + ck["field"]["agree"], ck["metadata"]["n"] + ck["field"]["n"]
        mod = ck["model"]
        out += ["", f"### Getting it right ([report](reports/harness-full-seed11.md), commit `{_commit(h)}`)", "",
                "| Measure | Result |", "|---|---|",
                f"| Requirement statuses exactly right | {st['exact']} of {st['requirement_instances']} "
                f"({_pct(st['exact_rate'])}) |",
                f"| Handed to a person instead (NEEDS_REVIEW) | {st.get('deferred_to_review', 0)} "
                f"({_pct(st['deferral_rate'])}) |",
                f"| Vouching kept, a non-critical doubt shown as a finding | {st.get('abstention_as_finding', 0)} |",
                f"| **Vouching when the truth says otherwise** | **{st.get('false_vouching', 0)}** |",
                f"| Raising an alarm when the truth is fine | {st.get('false_alarm', 0)} |",
                f"| Field and metadata judgements agreeing with the truth | {det[0]:,} of {det[1]:,} |",
                f"| Agreement-clause judgements, local model on CPU: agree / abstain / false PASS | {mod['agree']} / "
                f"{mod['abstained']} / {mod['false_pass']} of {mod['n']} |",
                f"| Documents classified right | {cl['correct']} of {cl['documents']} |",
                f"| Documents attached to the wrong creditor | {mt.get('wrong_match', 0)} |",
                f"| Planted instructions detected; model PASS on such documents | {sec['detected']} of "
                f"{sec['injected_documents']}; {sec['model_pass_on_hostile_documents']} |",
                f"| Re-index of every document: new OCR pages, new model calls | "
                f"{int(cost['reindex']['new_ocr_pages'])}, {int(cost['reindex']['new_model_calls'])} |"]
    lt, sh = _load("loadtest-full-seed11.json"), _load("shadow-ocr-full-seed11.json")
    if lt:
        out += ["", f"### Volume and cost ([load test](reports/loadtest-full-seed11.md), "
                f"[shadow OCR trial](reports/shadow-ocr-full-seed11.md), commit `{_commit(lt)}`)", "",
                "| Worker processes | Wall time | Uploads per minute | OCR pages paid | Statuses identical to one worker |",
                "|---|---|---|---|---|"]
        for x in lt["scaling"]:
            out.append(f"| {x['workers']} | {x['seconds']:.0f} s | {x['uploads_per_minute']:.0f} | {x['ocr_pages']} | "
                       f"{'yes' if x['statuses_identical_to_first'] else 'no'} |")
        b, c = lt.get("budget"), lt.get("crash")
        if b:
            out += ["", f"With a cap of {b['ocr_pages_per_day']} OCR pages a day for one tenant, "
                    f"{b['jobs_waiting_after_first_day']} jobs waited for the next day and none was dropped; the most "
                    f"paid on any day was {b['max_pages_any_day']}; final statuses identical: "
                    f"{'yes' if b['statuses_identical_to_unbudgeted'] else 'no'}."]
        if c:
            out[-1] += (f" A worker killed mid-job: {c['done']} of {c['jobs']} jobs done, {c['retried']} taken over, "
                        f"{c['dead']} lost; final statuses identical: "
                        f"{'yes' if c['statuses_identical_to_uninterrupted'] else 'no'}.")
    if sh:
        parts = [f"`{r['candidate']}`: field accuracy {r['canonical_accuracy']:.2f} -> {r['candidate_accuracy']:.2f}, "
                 f"{r['canonical_seconds_per_page']:.2f} -> {r['candidate_seconds_per_page']:.2f} s per page, switch "
                 f"{'allowed' if r['cutover_allowed'] else 'refused'}" for r in sh["results"]]
        out += ["", "Candidate OCR configurations run beside the canonical one, observe-only: " + "; ".join(parts) + "."]
    rs, rs50 = _load("review-sim-full-seed11.json"), _load("review-sim-full-seed11-audit50.json")
    if rs:
        q, a = rs["queue"], rs["accuracy"]
        decisions = sum(v for k, v in q["effort"].items() if k not in ("left", "did_not_land"))
        out += ["", f"### When it is not sure ([review simulation](reports/review-sim-full-seed11.md), "
                f"commit `{_commit(rs)}`)", "",
                f"A simulated reviewer who knows the truth worked the whole review queue: {decisions} decisions ("
                + ", ".join(f"{v} {k}" for k, v in sorted(q["effort"].items())) + f"). Afterwards "
                f"{q['after'].get('exact', 0)} of {q['pairs']} statuses were exactly right, and a full re-index changed "
                f"{q['changed_by_reindex']}.", "",
                f"A planted fault (the limitation period read as five years instead of three) made the engine vouch "
                f"wrongly {a['before'].get('false_vouching', 0)} times. At the default audit rate (10% of critical "
                "verdicts) the sample drew " +
                ("none of the false PASS verdicts" if not any(t["tripped_after"] for t in a["tenants"]) else
                 "some of them") +
                ", which the report shows is the expected outcome at that rate (" +
                ", ".join(f"tenant {t}: {p:.0%}" for t, p in a["detection_probability"]["10%"].items()
                          if any(x["tenant"] == int(t) and x["false_pass_verdicts"] for x in a["tenants"])) +
                " chance of drawing one)."]
        if rs50:
            b = rs50["accuracy"]
            affected = [t for t in b["tenants"] if t["false_pass_verdicts"]]
            tripped = [t for t in affected if t["tripped_after"]]
            link = "[report](reports/review-sim-full-seed11-audit50.md)"
            if tripped:
                out[-1] += (f" At 50% ({link}), the breaker tripped in {len(tripped)} of {len(affected)} affected "
                            f"tenants (after {', '.join(str(t['tripped_after']) for t in tripped)} audits), false "
                            f"vouching fell from {b['before'].get('false_vouching', 0)} to "
                            f"{b['after_audit'].get('false_vouching', 0)}, and after the confirmations "
                            f"{b['final'].get('exact', 0)} of {b['pairs']} statuses were right.")
            else:
                miss = 1.0
                for t in affected:
                    miss *= 1 - b["detection_probability"]["50%"][str(t["tenant"])]
                out[-1] += (f" At 50% ({link}) the sample still drew none of them, in any affected tenant: an outcome "
                            f"with a {miss:.0%} chance at that rate, reported as it fell. The breaker did not trip, "
                            f"and false vouching stayed at {b['after_audit'].get('false_vouching', 0)}: a breaker that "
                            "has not tripped is not a pass, and the audit rate trades review effort against time to "
                            "detection.")
    cal = _load("calibration-matching.json")
    if cal:
        rows = [f"down to {c['threshold']:g} at a {c['alpha']:.0%} wrong-attachment rate" for c in cal["calibration"]
                if c["threshold"] is not None]
        n = cal["calibration"][0]["n"]
        val = cal.get("validation") or {}
        today = next((v for v in val.get("results", []) if v["threshold"] == 92.0), None)
        out += ["", f"### Thresholds with a guarantee ([calibration](reports/calibration-matching.md), commit "
                f"`{_commit(cal)}`)", "",
                f"Learn then Test on {n:,} labelled documents certifies name-matching thresholds " + ", ".join(rows) +
                " (each with 95% confidence)." + (f" On the scanned world the hand-set 92 accepts {today['accepted']} of "
                                       f"{today['n']} documents matched by name, with {today['wrong']} wrong."
                                       if today else "")]
    act = _load("action-sim-full-seed11.json")
    if act:
        inv, tot = act["invariants"], act["totals"]
        out += ["", f"### From finding to action ([simulation](reports/action-sim-full-seed11.md), commit "
                f"`{_commit(act)}`)", "",
                f"{act['days']} simulated days: {inv['events']} status changes, each reported once and dispatched; "
                f"tasks " + ", ".join(f"{k} {v}" for k, v in sorted(act["tasks"].items())) +
                (f"; a creditor chase closed a median {act['days_to_close_chase_median']} days after it opened"
                 if act.get("days_to_close_chase_median") is not None else "") +
                f"; {act['reminders_written']} reminders (one a week at most); holds placed {tot.get('hold_placed', 0)}, "
                f"released {tot.get('hold_released', 0)}; {sum(act['deliveries'].values())} webhook messages "
                f"({', '.join(f'{k} {v}' for k, v in sorted(act['deliveries'].items()))}) to a receiver that was down "
                f"{act['outage']:.0%} of the time, each processed once "
                f"({sum(r['duplicates_ignored'] for r in act['receivers'].values())} repeats ignored). At most one open "
                f"task per requirement and one open hold per claim throughout; a dispatcher killed mid-event left "
                "nothing half done."]
    g = _load("assistant-golden-full-seed11.json")
    if g:
        bt = g["by_type"]
        f = bt.get("field", {})
        wrong = sum(bt.get(t, {}).get(k, 0) for t in ("field", "hostile")
                    for k in ("wrong_value", "wrong_version", "wrong_entity"))
        leaked = sum(c.get("leaked", 0) for c in bt.values())
        out += ["", f"### The assistant ([golden set](reports/assistant-golden-full-seed11.md), commit "
                f"`{_commit(g)}`)", "",
                f"{g['right']} of {g['questions']} questions answered right: field lookups with citations "
                f"({f.get('right', 0)} of {f.get('n', 0)}, including {f.get('unsettled', 0)} the engine had not settled, "
                f"answered as such), near-twin names, shared first words asked back, another tenant's creditors, "
                f"unanswerable questions, hostile documents and lists of lapsing authorities. Wrong values, wrong versions or wrong "
                f"creditors: {wrong}. Citations outside the tenant: {leaked}."]
    jc = REPORTS / "judge-comparison.md"
    if jc.exists():
        rows = [r for r in jc.read_text(encoding="utf-8").splitlines() if r.startswith("| ") and "| clean |" in r]
        cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
        if cells:
            out += ["", "### Why the judge asks twice ([comparison](reports/judge-comparison.md))", "",
                    "| Model | Strategy | Agree with truth | Abstain | False PASS |", "|---|---|---|---|---|"]
            for c in cells:
                out.append(f"| {c[0].replace('.gguf', '')} | {c[1]} | {c[4]} of {c[3]} | {c[5]} | {c[6]} |")
    floor = re.search(r"^MIN_TESTS = (\d+)", (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"), re.M)
    mutants = len(re.findall(r'^    \("', (ROOT / "scripts" / "prove_tests_fire.py").read_text(encoding="utf-8"),
                             re.M))
    out += ["", "### Delivery", "",
            f"At least {floor.group(1) if floor else '?'} tests (a run that collects fewer, or skips one without a "
            f"stated reason, fails); {mutants} safety rules broken on purpose, one at a time, each caught by the "
            "tests; the harness gates on every push; the same queue, tenancy and engine tests on Postgres; the pipeline "
            "itself checked by zizmor and actionlint; one required check that passes only when every job did "
            "([how a change ships](docs/delivery.md))."]
    return "\n".join(out)


def main() -> int:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    block = f"{START}\n{render()}\n{END}"
    if END in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    else:
        text = text.replace(START, block)
    readme.write_text(text, encoding="utf-8", newline="\n")
    print("README results rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
