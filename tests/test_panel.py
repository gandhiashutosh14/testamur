"""The adversarial false-PASS panel (panel.yaml): pages built to tempt one rule into a PASS it must not give, read by
the real field reader and judged by the real rule."""
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from testamur.checks import CheckContext, evaluate
from testamur.model import Extraction, Line, Page, Verdict
from testamur.pack import load_pack
from testamur.store import Engagement, Supplier, UploadRow
from testamur.understand import read_fields

CASES = yaml.safe_load((Path(__file__).parent / "panel.yaml").read_text(encoding="utf-8"))
REQUIREMENT = {"insurance_certificate": "insurance", "security_certificate": "security_certification",
               "bank_letter": "bank_details", "master_agreement": "master_agreement"}


def judge(case: dict):
    pack = load_pack()
    doc_type = case.get("doc_type", "insurance_certificate")
    ex = Extraction(pages=[Page(number=1, lines=[Line(text=t, page=1) for t in case["lines"]])], method="text_layer",
                    engine_version="panel")
    supplier = Supplier(id=1, tenant_id=1, legal_name="Brightwater Logistics Ltd", iban="GB33BUKB20201555555555")
    engagement = Engagement(id=1, tenant_id=1, business_unit_id=1, supplier_id=1, tier=case.get("tier", "high"),
                            engagement_type="data_processing", service_category=case.get("category", "logistics"),
                            start_date=date(2026, 2, 1), end_date=date(2027, 2, 1))
    upload = UploadRow(id=1, tenant_id=1, business_unit_id=1, sha256="x", filename="f", channel="staff_verified",
                       uploaded_at=datetime(2026, 1, 5))
    ctx = CheckContext(pack=pack, as_of=date(2026, 10, 1), engagement=engagement, supplier=supplier,
                       insured_party=supplier, via_parent=False, document_id=1,
                       fields=read_fields(pack.doc_types[doc_type], ex), uploads=[upload], source="text")
    return evaluate(case["check"], case.get("requirement", REQUIREMENT[doc_type]), ctx)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_no_adversarial_page_earns_a_pass(case):
    r = judge(case)
    assert r.verdict != Verdict.PASS, f"{case['id']}: {case['why']} -> PASS ({r.reason})"
    assert r.verdict == Verdict(case["expect"]), f"{case['id']}: expected {case['expect']}, got {r.verdict} ({r.reason})"


def test_the_panel_covers_every_kind_of_trap():
    assert len(CASES) >= 20 and len({c["id"] for c in CASES}) == len(CASES)
    assert {c["check"] for c in CASES} >= {"ins_limit_meets_tier", "ins_cover_current", "ins_name_matches", "msa_signed",
                                          "sec_scope_covers_service", "bank_iban_valid", "bank_iban_on_file"}
