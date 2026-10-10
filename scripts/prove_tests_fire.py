"""Prove the safety tests fire: break each safety rule on purpose and require the test suite to go red.

A test that asserts "this must fail" is only trusted once it has been seen failing for the right reason. Each mutant
below disables one safety property. The script applies it, runs the tests that guard it, restores the source, and
fails if any mutant survives (the tests stayed green with the property broken).

Usage: python scripts/prove_tests_fire.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MUTANTS = [
    ("uncertainty on a critical check rounds up", "testamur/status.py",
     "if any(r.verdict == Verdict.ABSTAIN for r in critical):", "if False:", "tests/test_status_policy.py"),
    ("a requirement nobody checked counts as met", "testamur/status.py",
     "if not any(r.verdict in (Verdict.PASS, Verdict.FAIL) for r in results):", "if False:",
     "tests/test_status_policy.py"),
    ("a pending document no longer blocks bad news", "testamur/status.py",
     "if pending_review and status in", "if False and status in", "tests/test_status_policy.py"),
    ("a pending document that may be the creditor's own leaves trustee cover vouching", "testamur/status.py",
     "if pending_review and via_trustee and status in VOUCHING:", "if False:", "tests/test_status_policy.py"),
    ("hostile evidence earns a vouching status", "testamur/status.py",
     "if hostile and results is not None and status in VOUCHING:", "if False:", "tests/test_status_policy.py"),
    ("a budget check does not reserve what it allows", "testamur/budget.py",
     "    session.add(row)\n", "    pass\n", "tests/test_jobs.py"),
    ("an oversized document runs on a day others already spent", "testamur/budget.py",
     "oversized_alone = 0 < cap < wanted and used == 0", "oversized_alone = 0 < cap < wanted", "tests/test_jobs.py"),
    ("a second worker pays for a file another is reading", "testamur/engine.py",
     "if lock is not None and (now - lock.locked_at).total_seconds() < LOCK_SECONDS:", "if False:",
     "tests/test_jobs.py"),
    ("an expiry check passes everything", "testamur/checks.py",
     "if fv.value >= target:", "if True:", "tests/test_checks.py"),
    ("contradictory values are judged anyway", "testamur/checks.py",
     "if name in ctx.fields.conflicts:", "if False:", "tests/test_checks.py"),
    ("two amounts on one line are read as the first", "testamur/textnorm.py",
     "if len(_CURRENCY_MARK.findall(s)) > 1 or big > 1:", "if False:", "tests/test_panel.py"),
    ("a template placeholder counts as a signature", "testamur/checks.py",
     " or _PLACEHOLDER.match(str(fv.value).strip()):", ":", "tests/test_panel.py"),
    ("powers that name voting only to exclude it count as granting it", "testamur/checks.py",
     "if hit and _EXCLUSION.search(fv.value):", "if False:", "tests/test_panel.py"),
    ("malformed digit grouping is accepted", "testamur/textnorm.py",
     "if not num.isdigit() and not (_GROUPED.match(num) or _GROUPED_IN.match(num)):", "if False:",
     "tests/test_parsing.py"),
    ("thousands and lakh grouping mixed in one number are accepted", "testamur/textnorm.py",
     '_GROUPED_IN = re.compile(r"^\\d{1,2}(?:,\\d{2})*,\\d{3}$")', '_GROUPED_IN = re.compile(r"^[\\d,]+$")',
     "tests/test_parsing.py"),
    ("an amount in words is read with its scales out of order", "testamur/textnorm.py",
     "if current == 0 or (last_scale is not None and scale >= last_scale):", "if current == 0:",
     "tests/test_parsing.py"),
    ("a claim above the corporate debtor's books passes", "testamur/checks.py",
     'if amount <= Decimal(books) * Decimal(str(1 + p["tolerance"])):', "if True:", "tests/test_checks.py"),
    ("words that disagree with the figures pass", "testamur/checks.py",
     "if amount is not None and amount == words.value:", "if amount is not None:", "tests/test_checks.py"),
    ("a default outside limitation passes", "testamur/checks.py",
     "if fv.value >= limit:", "if True:", "tests/test_checks.py"),
    ("a PAN that is not the GSTIN's passes", "testamur/checks.py",
     "if compact(pan.value) == inside:", "if True:", "tests/test_checks.py"),
    ("an IFSC of another bank passes", "testamur/checks.py",
     "if code[:4] == expected:", "if True:", "tests/test_checks.py"),
    ("any close name is accepted as an exact match", "testamur/match.py",
     "if best[0] >= EXACT > second[0]:", "if best[0] >= ACCEPT:", "tests/test_match.py"),
    ("a model's quote is not checked against the document", "testamur/modeltier.py",
     "if quote_in(q, excerpt)), None)", "if q), None)", "tests/test_model_tier.py"),
    ("answers that depend on statement order are accepted", "testamur/modeltier.py",
     "if a[\"statement\"] == 1 and b[\"statement\"] == 2:", "if a[\"statement\"] == 1:", "tests/test_model_tier.py"),
    ("hostile documents are shown to the model", "testamur/engine.py",
     "        if doc.hostile:\n", "        if False:\n", "tests/test_engine.py"),
    ("a transient failure overwrites a conclusive verdict", "testamur/engine.py",
     "and r.transient:", "and False:", "tests/test_engine.py"),
    ("a person's verdict is ignored when the engine runs again", "testamur/engine.py",
     "    if decision is not None:", "    if False:", "tests/test_reviews.py"),
    ("a decision outlives a change to the rule it was made under", "testamur/engine.py",
     "if d.rule_digest != self.pack.rule_digest(d.check_id):", "if False:", "tests/test_reviews.py"),
    ("a person's reading of a field is ignored", "testamur/engine.py",
     "            fields.values[c.field] = FieldValue(", "            fields.values[c.field + '~'] = FieldValue(",
     "tests/test_reviews.py"),
    ("a person's decision about whose document it is is lost on re-index", "testamur/engine.py",
     "        if disposition is not None:                       # a person said whose document it is",
     "        if False:", "tests/test_reviews.py"),
    ("a skip outlives the failure that decided it", "testamur/engine.py",
     "already decides the outcome\",\n                       transient=True)", "already decides the outcome\")",
     "tests/test_reviews.py"),
    ("a date-dependent verdict from yesterday is reused today", "testamur/engine.py",
     " and (v.as_of is None or v.as_of == self.as_of)", "", "tests/test_reviews.py"),
    ("a person's PASS on a dated check outlives its day", "testamur/engine.py",
     "if d.verdict == Verdict.PASS and d.as_of is not None and d.as_of != self.as_of:", "if False:",
     "tests/test_reviews.py"),
    ("a cleared hostile document is shown to the model", "testamur/engine.py",
     "        if doc.hostile:\n", "        if doc.hostile and not doc.cleared:\n", "tests/test_reviews.py"),
    ("a check that breached its budget still vouches", "testamur/engine.py",
     "if demoted and r.verdict == Verdict.PASS:", "if False:", "tests/test_accuracy.py"),
    ("a confirmed false PASS on a critical check does not breach it", "testamur/accuracy.py",
     'if (spec.severity == Severity.CRITICAL and c["incidents"]) or (lower is not None and lower > budget):',
     "if lower is not None and lower > budget:", "tests/test_accuracy.py"),
    ("audits made under an older rule still count", "testamur/accuracy.py",
     "r.rule_digest == digests[r.check_id] and r.id >", "r.id >", "tests/test_accuracy.py"),
    ("reviews of abstentions count toward accuracy", "testamur/accuracy.py",
     'COUNTED = ("audit", "confirm")', 'COUNTED = ("audit", "confirm", "queue")', "tests/test_accuracy.py"),
    ("a status change goes unreported", "testamur/engine.py",
     "        if before != (status, failing):", "        if False:", "tests/test_actions.py"),
    ("every intermediate status of a bulk load is acted on", "testamur/actions.py",
     "            if _superseded(s, ev):", "            if False:", "tests/test_actions.py"),
    ("a reminder ignores its cooldown", "testamur/actions.py",
     "if last is not None and now - last.created_at < timedelta(days=policy.notify_cooldown_days):", "if False:",
     "tests/test_actions.py"),
    ("a task stays open after the evidence is put right", "testamur/actions.py",
     "    elif task is not None:", "    elif False:", "tests/test_actions.py"),
    ("a hold outlives the evidence being put right", "testamur/actions.py",
     "elif hold is not None and not holding:", "elif False:", "tests/test_actions.py"),
    ("a replayed webhook message is accepted", "testamur/actions.py",
     "if abs(now_ts - ts) > tolerance:", "if False:", "tests/test_actions.py"),
    ("a repeated webhook message is processed again", "testamur/actions.py",
     "if wid in self.seen:", "if False:", "tests/test_actions.py"),
    ("the assistant cites a quote that is not on the page", "testamur/assistant.py",
     "if quote_in(q, text) or (len(q) < 20 and q.casefold() in text.casefold()):", "if True:",
     "tests/test_assistant.py"),
    ("the assistant quotes text aimed at automated readers", "testamur/assistant.py",
     "if not any(h.search(text) for h in hostile):", "if True:", "tests/test_assistant.py"),
    ("the assistant guesses between creditors that share a name", "testamur/assistant.py",
     "            if len(first) > 1:", "            if False:", "tests/test_assistant.py"),
    ("a tool the tenant did not grant still answers", "testamur/mcp_server.py",
     "        agents.require(engine.store, tenant_id, tool)", "        pass", "tests/test_mcp.py"),
    ("a declined proposal is applied anyway", "testamur/agents.py",
     "        if not confirm:", "        if False:", "tests/test_mcp.py"),
    ("a proposal can be applied twice", "testamur/agents.py",
     '        if p.status != "proposed":', "        if False:", "tests/test_mcp.py"),
    ("a change to the safety core is not classified high risk", "scripts/risk.py",
     '            level = "high"', '            level = "medium"', "tests/test_risk.py"),
    ("tenant filtering is removed from reads", "testamur/store.py",
     "    state.statement = state.statement.options(", "    return\n    state.statement = state.statement.options(",
     "tests/test_tenancy.py"),
]


def main() -> int:
    survived = []
    for name, rel, before, after, tests in MUTANTS:
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(before) != 1:
            print(f"BROKEN MUTANT {name!r}: the target text occurs {original.count(before)} times in {rel}")
            return 2
        try:
            path.write_text(original.replace(before, after), encoding="utf-8", newline="\n")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests],
                               cwd=ROOT, capture_output=True, text=True)
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
        caught = r.returncode != 0
        print(f"{'caught  ' if caught else 'SURVIVED'} {name}  ({tests})")
        if not caught:
            survived.append(name)
    print(f"{len(MUTANTS) - len(survived)} of {len(MUTANTS)} safety mutants caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
