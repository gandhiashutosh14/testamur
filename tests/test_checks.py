"""Deterministic check rules on hand-built field values: no page reading, no ambiguity about what was found."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from testamur.checks import CheckContext, evaluate
from testamur.ids import gstin_with_check
from testamur.model import FieldValue, Verdict
from testamur.pack import load_pack
from testamur.store import Claim, Creditor, Matter, UploadRow
from testamur.understand import Fields

AS_OF = date(2026, 10, 1)
GSTIN = gstin_with_check(27, "AAKCA1234K")


def ctx(values=None, conflicts=None, channel="staff_upload", uploaded=datetime(2026, 5, 10)):
    fields = Fields(values={k: FieldValue(name=k, value=v, raw=str(v), page=1, line=f"{k}: {v}")
                            for k, v in (values or {}).items()},
                    conflicts={k: [FieldValue(name=k, value=v, raw=str(v), page=i + 1, line=str(v))
                                   for i, v in enumerate(vs)] for k, vs in (conflicts or {}).items()})
    creditor = Creditor(id=1, tenant_id=1, legal_name="Anvaya Fincorp Ltd", account_number="004512345678",
                        ifsc="NRMX0004521")
    claim = Claim(id=1, tenant_id=1, matter_id=1, creditor_id=1, tier="high", claim_type="secured",
                  facility_type="working_capital", book_balance=123_456_000, sanctioned=250_000_000)
    matter = Matter(id=1, tenant_id=1, name="Pench Valley Polymers Pvt Ltd", debtor_cin="U25209MH2011PTC123456",
                    commencement=date(2026, 5, 1), announced=date(2026, 5, 3), claims_due=date(2026, 5, 15))
    up = UploadRow(id=1, tenant_id=1, matter_id=1, sha256="x", filename="f", channel=channel, uploaded_at=uploaded)
    return CheckContext(pack=load_pack(), as_of=AS_OF, claim=claim, creditor=creditor, filing_party=creditor,
                        via_trustee=False, matter=matter, document_id=1, fields=fields, uploads=[up], source="text")


def v(check, c, rid="authority"):
    return evaluate(check, rid, c)


def test_a_lapsed_authority_fails_and_is_marked_lapsed():
    r = v("auth_current", ctx({"valid_until": date(2026, 9, 1)}))
    assert r.verdict == Verdict.FAIL and r.lapsed and "fresh authority" in r.fix_hint
    assert v("auth_current", ctx({"valid_until": date(2026, 10, 1)})).verdict == Verdict.PASS
    renewal = v("auth_renewal_due", ctx({"valid_until": date(2026, 10, 20)}))
    assert renewal.verdict == Verdict.FAIL and not renewal.lapsed
    # a lapse is reported once, by the expiry check, not again as "renewal due"
    assert v("auth_renewal_due", ctx({"valid_until": date(2026, 9, 1)})).verdict == Verdict.SKIPPED


def test_a_document_that_contradicts_itself_is_not_judged():
    r = v("auth_current", ctx(conflicts={"valid_until": [date(2027, 3, 1), date(2026, 5, 1)]}))
    assert r.verdict == Verdict.ABSTAIN and "two different values" in r.reason


def test_missing_values_abstain_rather_than_pass():
    assert v("claim_within_books", ctx({}), "proof_of_claim").verdict == Verdict.ABSTAIN
    assert v("claim_name_matches", ctx({}), "proof_of_claim").verdict == Verdict.ABSTAIN
    assert v("claim_not_time_barred", ctx({}), "proof_of_claim").verdict == Verdict.ABSTAIN


def test_a_default_more_than_three_years_before_commencement_is_time_barred():
    inside = v("claim_not_time_barred", ctx({"default_date": date(2023, 5, 1)}), "proof_of_claim")
    assert inside.verdict == Verdict.PASS                          # exactly three years: still within limitation
    barred = v("claim_not_time_barred", ctx({"default_date": date(2023, 4, 30)}), "proof_of_claim")
    assert barred.verdict == Verdict.FAIL and "section 18" in barred.fix_hint and not barred.lapsed


def test_a_claim_may_be_below_the_books_but_not_above_them():
    rupees = lambda n: (Decimal(n), "INR")  # noqa: E731
    assert v("claim_within_books", ctx({"amount_claimed": rupees(120_000_000)}), "proof_of_claim").verdict == Verdict.PASS
    assert v("claim_within_books", ctx({"amount_claimed": rupees(125_000_000)}), "proof_of_claim").verdict == Verdict.PASS
    over = v("claim_within_books", ctx({"amount_claimed": rupees(140_000_000)}), "proof_of_claim")
    assert over.verdict == Verdict.FAIL and "Rs. 12,34,56,000" in over.fix_hint


def test_words_must_agree_with_figures():
    same = {"amount_claimed": (Decimal(123_456_000), "INR"), "amount_in_words": Decimal(123_456_000)}
    assert v("claim_words_match", ctx(same), "proof_of_claim").verdict == Verdict.PASS
    off = {"amount_claimed": (Decimal(123_456_000), "INR"), "amount_in_words": Decimal(122_456_000)}
    r = v("claim_words_match", ctx(off), "proof_of_claim")
    assert r.verdict == Verdict.FAIL and "Twelve Crore Twenty-Four Lakh" in r.fix_hint


def test_the_pan_must_be_the_one_inside_the_gstin():
    assert v("claim_gstin_valid", ctx({"gstin": GSTIN}), "proof_of_claim").verdict == Verdict.PASS
    assert v("claim_gstin_valid", ctx({"gstin": GSTIN[:-1] + "0"}), "proof_of_claim").verdict == (
        Verdict.PASS if GSTIN[-1] == "0" else Verdict.FAIL)
    assert v("claim_pan_in_gstin", ctx({"gstin": GSTIN, "pan": "AAKCA1234K"}), "proof_of_claim").verdict == Verdict.PASS
    other = v("claim_pan_in_gstin", ctx({"gstin": GSTIN, "pan": "AAKCA9234K"}), "proof_of_claim")
    assert other.verdict == Verdict.FAIL and other.fix_hint


def test_a_form_dated_before_the_announcement_was_prepared_for_something_else():
    early = v("claim_dated_after_announcement", ctx({"claim_date": date(2026, 4, 20)}), "proof_of_claim")
    assert early.verdict == Verdict.FAIL
    assert v("claim_dated_after_announcement", ctx({"claim_date": date(2026, 5, 3)}),
             "proof_of_claim").verdict == Verdict.PASS


def test_a_late_claim_is_a_finding_with_the_days_late():
    late = v("received_by_due_date", ctx(uploaded=datetime(2026, 5, 20)), "proof_of_claim")
    assert late.verdict == Verdict.FAIL and "5 days after" in late.fix_hint
    assert v("received_by_due_date", ctx(uploaded=datetime(2026, 5, 15, 17)), "proof_of_claim").verdict == Verdict.PASS


@pytest.mark.parametrize("found, expected", [("Anvaya Fincorp Limited", Verdict.PASS),
                                             ("Shree Anvaya Fincorp Ltd", Verdict.ABSTAIN),
                                             ("Ojasvi Credit Pvt Ltd", Verdict.FAIL)])
def test_names_pass_fail_or_go_to_a_person(found, expected):
    assert v("auth_creditor_matches", ctx({"creditor_name": found})).verdict == expected


def test_a_representative_who_may_only_file_cannot_vote():
    voting = ctx({"powers": "To attend and vote at meetings of the committee of creditors"})
    assert v("auth_covers_voting", voting).verdict == Verdict.PASS
    filing = v("auth_covers_voting", ctx({"powers": "To sign and submit the proof of claim"}))
    assert filing.verdict == Verdict.FAIL
    excluded = ctx({"powers": "To attend meetings of the committee of creditors, excluding voting on any plan"})
    assert v("auth_covers_voting", excluded).verdict == Verdict.ABSTAIN


def test_a_charge_must_be_the_creditors_over_the_debtor_and_cover_the_facility():
    rid = "security_interest"
    assert v("charge_on_debtor", ctx({"company": "Pench Valley Polymers Private Limited"}), rid).verdict == Verdict.PASS
    assert v("charge_on_debtor", ctx({"company": "Satpura Foundry Works Ltd"}), rid).verdict == Verdict.FAIL
    assets = ctx({"assets": "Hypothecation of stock and book debts, present and future"})
    assert v("charge_covers_assets", assets, rid).verdict == Verdict.PASS
    assert v("charge_covers_assets", ctx({"assets": "Hypothecation of motor vehicles"}), rid).verdict == Verdict.FAIL
    under = v("charge_amount_covers", ctx({"amount_secured": (Decimal(100_000_000), "INR")}), rid)
    assert under.verdict == Verdict.FAIL and "unsecured" in under.fix_hint
    assert v("charge_registrar_recognised", ctx({"registrar": "Online Charge Registry"}), rid).verdict == Verdict.FAIL


def test_bank_details_must_match_the_record_name_their_bank_and_arrive_verified():
    rid = "bank_details"
    assert v("bank_account_on_file", ctx({"account_number": "0045 1234 5678"}), rid).verdict == Verdict.PASS
    changed = v("bank_account_on_file", ctx({"account_number": "991234567812"}), rid)
    assert changed.verdict == Verdict.FAIL and "fraud" in changed.fix_hint
    own = ctx({"ifsc": "NRMX0004521", "bank_name": "Narmada Commercial Bank"})
    assert v("bank_ifsc_valid", own, rid).verdict == Verdict.PASS
    other = ctx({"ifsc": "DCMX0004521", "bank_name": "Narmada Commercial Bank"})
    assert v("bank_ifsc_valid", other, rid).verdict == Verdict.FAIL
    assert v("bank_channel_verified", ctx(channel="creditor_portal"), rid).verdict == Verdict.FAIL
    assert v("bank_channel_verified", ctx(channel="staff_verified"), rid).verdict == Verdict.PASS


def test_an_optional_identifier_that_is_absent_is_skipped_not_passed():
    assert v("claim_lei_valid", ctx({}), "proof_of_claim").verdict == Verdict.SKIPPED
    assert v("claim_lei_valid", ctx({"lei": "5493001KJTIIGC8Y1R00"}), "proof_of_claim").verdict == Verdict.FAIL


def test_an_agreement_needs_both_signatures():
    one = ctx({"lender_signatory": "Ananya Kulkarni, Authorised Signatory, 02.03.2023"})
    assert v("facility_signed", one, "evidence_of_debt").verdict == Verdict.FAIL
    both = ctx({"lender_signatory": "Ananya Kulkarni", "borrower_signatory": "Rohan Deshpande"})
    assert v("facility_signed", both, "evidence_of_debt").verdict == Verdict.PASS
