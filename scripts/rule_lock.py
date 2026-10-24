"""Record each check rule's source fingerprint and version in tests/rule_versions.lock.json.

Run it after changing a rule AND bumping its version in checks.RULE_VERSIONS; the test that reads the lock fails when a
rule's code changed but its version did not, because a person's decision about a check is bound to the version: a
rule that means something new must not keep decisions made under the old meaning."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from testamur import modeltier
from testamur.checks import RULE_VERSIONS, RULES

LOCK = Path(__file__).resolve().parents[1] / "tests" / "rule_versions.lock.json"


def sources() -> dict[str, str]:
    """The code that decides each rule: the rule function (and, for the model tier, its judgement)."""
    fns = {name: [fn] for name, fn in RULES.items()}
    fns["clause_present"] = [modeltier.judge, modeltier._ask]
    out = {}
    for name, group in fns.items():
        src = "\n".join(inspect.getsource(fn) for fn in group).replace("\r\n", "\n")
        out[name] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
    return out


def lock() -> dict[str, dict]:
    return {name: {"source": h, "version": RULE_VERSIONS.get(name)} for name, h in sorted(sources().items())}


if __name__ == "__main__":
    LOCK.write_text(json.dumps(lock(), indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(RULE_VERSIONS)} rules -> {LOCK}")
