"""Deterministic check rules on hand-built field values: no page reading, no ambiguity about what was found."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from testamur.checks import CheckContext, evaluate
from testamur.model import FieldValue, Verdict
from testamur.pack import load_pack
from testamur.store import Engagement, Supplier, UploadRow
from testamur.understand import Fields

AS_OF = date(2026, 10, 1)


def ctx(values=None, conflicts=None, tier="high", channel="staff_upload", uploaded=datetime(2026, 1, 5)):
    fields = Fields(values={k: FieldValue(name=k, value=v, raw=str(v), page=1, line=f"{k}: {v}")
                            for k, v in (values or {}).items()},
                    conflicts={k: [FieldValue(name=k, value=v, raw=str(v), page=i + 1, line=str(v))
                                   for i, v in enumerate(vs)] for k, vs in (conflicts or {}).items()})
    supplier = Supplier(id=1, tenant_id=1, legal_name="Brightwater Logistics Ltd", iban="GB33BUKB20201555555555")
    eng = Engagement(id=1, tenant_id=1, business_unit_id=1, supplier_id=1, tier=tier, engagement_type="services",
                     service_category="logistics", start_date=date(2026, 2, 1), end_date=date(2027, 2, 1))
    up = UploadRow(id=1, tenant_id=1, business_unit_id=1, sha256="x", filename="f", channel=channel,
                   uploaded_at=uploaded)
    return CheckContext(pack=load_pack(), as_of=AS_OF, engagement=eng, supplier=supplier, insured_party=supplier,
                        via_parent=False, document_id=1, fields=fields, uploads=[up], source="text")


def v(check, c, rid="insurance"):
    return evaluate(check, rid, c)


def test_a_lapsed_expiry_fails_and_is_marked_lapsed():
    r = v("ins_cover_current", ctx({"expiry_date": date(2026, 9, 1)}))
    assert r.verdict == Verdict.FAIL and r.lapsed and "Request the renewed certificate" in r.fix_hint
    assert v("ins_cover_current", ctx({"expiry_date": date(2026, 10, 1)})).verdict == Verdict.PASS
    renewal = v("ins_renewal_due", ctx({"expiry_date": date(2026, 10, 20)}))
    assert renewal.verdict == Verdict.FAIL and not renewal.lapsed
    # a lapse is reported once, by the expiry check, not again as "renewal due"
    assert v("ins_renewal_due", ctx({"expiry_date": date(2026, 9, 1)})).verdict == Verdict.SKIPPED


def test_a_document_that_contradicts_itself_is_not_judged():
    r = v("ins_cover_current", ctx(conflicts={"expiry_date": [date(2027, 3, 1), date(2026, 5, 1)]}))
    assert r.verdict == Verdict.ABSTAIN and "two different values" in r.reason


def test_missing_values_abstain_rather_than_pass():
    assert v("ins_limit_meets_tier", ctx({})).verdict == Verdict.ABSTAIN
    assert v("ins_name_matches", ctx({})).verdict == Verdict.ABSTAIN


def test_limits_compare_per_tier_and_refuse_to_convert_currency():
    assert v("ins_limit_meets_tier", ctx({"gl_limit": (Decimal(5_000_000), "EUR")}, tier="high")).verdict == Verdict.PASS
    assert v("ins_limit_meets_tier", ctx({"gl_limit": (Decimal(5_000_000), "EUR")}, tier="critical")).verdict == Verdict.FAIL
    assert v("ins_limit_meets_tier", ctx({"gl_limit": (Decimal(9_000_000), "GBP")})).verdict == Verdict.ABSTAIN


@pytest.mark.parametrize("found, expected", [("Brightwater Logistics Limited", Verdict.PASS),
                                             ("Brightwater Logistic Services Ltd", Verdict.ABSTAIN),
                                             ("Kestrel Freight Ltd", Verdict.FAIL)])
def test_names_pass_fail_or_go_to_a_person(found, expected):
    assert v("ins_name_matches", ctx({"insured_name": found})).verdict == expected


def test_bank_details_must_match_the_record_and_arrive_verified():
    assert v("bank_iban_on_file", ctx({"iban": "GB33 BUKB 2020 1555 5555 55"}), "bank_details").verdict == Verdict.PASS
    changed = v("bank_iban_on_file", ctx({"iban": "GB29NMBK60161331926819"}), "bank_details")
    assert changed.verdict == Verdict.FAIL and "fraud" in changed.fix_hint
    assert v("bank_channel_verified", ctx(channel="supplier_portal"), "bank_details").verdict == Verdict.FAIL
    assert v("bank_channel_verified", ctx(channel="staff_verified"), "bank_details").verdict == Verdict.PASS


def test_an_optional_identifier_that_is_absent_is_skipped_not_passed():
    assert v("tax_lei_valid", ctx({}), "tax_registration").verdict == Verdict.SKIPPED
    assert v("tax_lei_valid", ctx({"lei": "5493001KJTIIGC8Y1R00"}), "tax_registration").verdict == Verdict.FAIL


def test_an_agreement_needs_both_signatures():
    one = ctx({"supplier_signatory": "Anika Rao, Director, 2 March 2026"})
    assert v("msa_signed", one, "master_agreement").verdict == Verdict.FAIL
    both = ctx({"supplier_signatory": "Anika Rao", "customer_signatory": "Tomas Brandt"})
    assert v("msa_signed", both, "master_agreement").verdict == Verdict.PASS
