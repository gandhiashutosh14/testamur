"""The pack's identities: the engine version must not depend on the platform a pack was checked out on, and a
check's rule digest must change exactly when what the check means changes."""
from testamur.pack import PACKS, load_pack, pack_digest


def test_the_pack_digest_ignores_line_endings():
    assert pack_digest(b"name: x\r\nversion: 1\r\n") == pack_digest(b"name: x\nversion: 1\n")
    lf = (PACKS / "insolvency_claims" / "pack.yaml").read_bytes().replace(b"\r\n", b"\n")
    assert load_pack().digest == pack_digest(lf) == pack_digest(lf.replace(b"\n", b"\r\n"))


def test_a_rule_digest_changes_with_the_rule_and_its_tables_only():
    pack = load_pack()
    reworded = pack.model_copy(deep=True)
    reworded.checks["charge_registrar_recognised"].hint = "Different words for people."
    assert reworded.rule_digest("charge_registrar_recognised") == pack.rule_digest("charge_registrar_recognised")
    widened = pack.model_copy(deep=True)
    widened.registrar_offices.append("Registrar of Companies, Central Circle")
    assert widened.rule_digest("charge_registrar_recognised") != pack.rule_digest("charge_registrar_recognised")
    assert widened.rule_digest("bank_ifsc_valid") == pack.rule_digest("bank_ifsc_valid")
    loosened = pack.model_copy(deep=True)
    loosened.checks["auth_creditor_matches"].params["pass_at"] = 85
    assert loosened.rule_digest("auth_creditor_matches") != pack.rule_digest("auth_creditor_matches")
