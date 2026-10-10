"""The adversarial false-PASS panel (panel.yaml): pages built to tempt one rule into a PASS it must not give, read by
the real field reader and judged by the real rule."""
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from testamur.checks import CheckContext, evaluate
from testamur.model import Extraction, Line, Page, Verdict
from testamur.pack import load_pack
from testamur.store import Claim, Creditor, Matter, UploadRow
from testamur.understand import read_fields

CASES = yaml.safe_load((Path(__file__).parent / "panel.yaml").read_text(encoding="utf-8"))
REQUIREMENT = {"claim_form": "proof_of_claim", "authority_letter": "authority", "charge_certificate": "security_interest",
               "bank_mandate": "bank_details", "facility_agreement": "evidence_of_debt"}


def judge(case: dict):
    pack = load_pack()
    doc_type = case.get("doc_type", "authority_letter")
    ex = Extraction(pages=[Page(number=1, lines=[Line(text=t, page=1) for t in case["lines"]])], method="text_layer",
                    engine_version="panel")
    creditor = Creditor(id=1, tenant_id=1, legal_name="Anvaya Fincorp Ltd", account_number="004512345678",
                        ifsc="NRMX0004521")
    claim = Claim(id=1, tenant_id=1, matter_id=1, creditor_id=1, tier=case.get("tier", "high"), claim_type="secured",
                  facility_type=case.get("facility", "working_capital"), book_balance=123_456_000,
                  sanctioned=250_000_000)
    matter = Matter(id=1, tenant_id=1, name="Pench Valley Polymers Pvt Ltd", debtor_cin="U25209MH2011PTC123456",
                    commencement=date(2026, 5, 1), announced=date(2026, 5, 3), claims_due=date(2026, 5, 15))
    upload = UploadRow(id=1, tenant_id=1, matter_id=1, sha256="x", filename="f", channel="staff_verified",
                       uploaded_at=datetime(2026, 5, 10))
    ctx = CheckContext(pack=pack, as_of=date(2026, 10, 1), claim=claim, creditor=creditor, filing_party=creditor,
                       via_trustee=False, matter=matter, document_id=1,
                       fields=read_fields(pack.doc_types[doc_type], ex), uploads=[upload], source="text")
    return evaluate(case["check"], case.get("requirement", REQUIREMENT[doc_type]), ctx)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_no_adversarial_page_earns_a_pass(case):
    r = judge(case)
    assert r.verdict != Verdict.PASS, f"{case['id']}: {case['why']} -> PASS ({r.reason})"
    assert r.verdict == Verdict(case["expect"]), f"{case['id']}: expected {case['expect']}, got {r.verdict} ({r.reason})"


def test_the_panel_covers_every_kind_of_trap():
    assert len(CASES) >= 25 and len({c["id"] for c in CASES}) == len(CASES)
    assert {c["check"] for c in CASES} >= {"claim_within_books", "claim_words_match", "claim_not_time_barred",
                                          "claim_pan_in_gstin", "auth_current", "auth_creditor_matches",
                                          "auth_covers_voting", "facility_signed", "charge_amount_covers",
                                          "charge_on_debtor", "bank_ifsc_valid", "bank_account_on_file"}
