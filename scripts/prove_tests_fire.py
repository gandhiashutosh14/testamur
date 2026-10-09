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
    ("hostile evidence earns a vouching status", "testamur/status.py",
     "if hostile and results is not None and status in VOUCHING:", "if False:", "tests/test_status_policy.py"),
    ("an expiry check passes everything", "testamur/checks.py",
     "if fv.value >= target:", "if True:", "tests/test_checks.py"),
    ("contradictory values are judged anyway", "testamur/checks.py",
     "if name in ctx.fields.conflicts:", "if False:", "tests/test_checks.py"),
    ("malformed digit grouping is accepted", "testamur/textnorm.py",
     "        g = _GROUPED.match(num)", "        g = True", "tests/test_parsing.py"),
    ("mixed grouping separators are accepted", "testamur/textnorm.py",
     "if len({c for c in num if not c.isdigit()}) != 1:", "if False:", "tests/test_parsing.py"),
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
            path.write_text(original.replace(before, after), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests],
                               cwd=ROOT, capture_output=True, text=True)
        finally:
            path.write_text(original, encoding="utf-8")
        caught = r.returncode != 0
        print(f"{'caught  ' if caught else 'SURVIVED'} {name}  ({tests})")
        if not caught:
            survived.append(name)
    print(f"{len(MUTANTS) - len(survived)} of {len(MUTANTS)} safety mutants caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
