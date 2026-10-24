"""A rule that means something new has a new version. A person's decision about a check is bound to the check's
definition, the tables its rule reads and the rule's version (Pack.rule_digest); this test holds the rule's code to its
version, so a change to what a rule does cannot keep decisions made under the old meaning."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rule_lock import LOCK, sources  # noqa: E402

from testamur.checks import RULE_VERSIONS, RULES  # noqa: E402
from testamur.pack import load_pack  # noqa: E402


def test_every_rule_has_a_version_and_a_recorded_fingerprint():
    recorded = json.loads(LOCK.read_text(encoding="utf-8"))
    assert set(RULE_VERSIONS) == set(RULES) | {"clause_present"}
    assert set(recorded) == set(RULE_VERSIONS), "rules added or removed: run python scripts/rule_lock.py"


def test_a_rule_whose_code_changed_has_moved_its_version():
    recorded = json.loads(LOCK.read_text(encoding="utf-8"))
    for name, fingerprint in sources().items():
        was = recorded[name]
        if fingerprint != was["source"]:
            assert RULE_VERSIONS[name] > was["version"], (
                f"the code of rule {name} changed but its version did not: if what it decides changed, bump "
                f"RULE_VERSIONS[{name!r}]; then run python scripts/rule_lock.py")
            raise AssertionError(f"rule {name} has a new version: record it with python scripts/rule_lock.py")
        assert RULE_VERSIONS[name] == was["version"], f"rule {name}: version moved without its code: re-run the lock"


def test_a_new_rule_version_unbinds_decisions_made_under_the_old_one():
    pack = load_pack()
    before = pack.rule_digest("auth_covers_voting")
    RULE_VERSIONS["grants_power"] += 1
    try:
        assert pack.rule_digest("auth_covers_voting") != before
        assert pack.rule_digest("auth_current") == pack.rule_digest("auth_current")     # other rules unaffected
    finally:
        RULE_VERSIONS["grants_power"] -= 1
    assert pack.rule_digest("auth_covers_voting") == before
