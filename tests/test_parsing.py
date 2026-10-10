from datetime import date
from decimal import Decimal

import pytest

from testamur.ids import (
    cin_valid,
    gstin_valid,
    gstin_with_check,
    ifsc_valid,
    lei_valid,
    lei_with_check,
    pan_of_gstin,
    pan_valid,
)
from testamur.textnorm import (
    after_label,
    format_inr,
    name_similarity,
    parse_date,
    parse_money,
    parse_rupee_words,
    rupees_in_words,
)


def test_identifiers_validate_and_reject_single_character_errors():
    lei = lei_with_check("5493001KJTIIGC8Y1R")
    assert lei_valid(lei)
    assert not lei_valid(lei[:-1] + ("0" if lei[-1] != "0" else "1"))
    assert gstin_valid("27AAPFU0939F1ZV")                 # the example the GST portal's own guides use
    assert gstin_valid("27 AAPFU 0939 F1ZV")              # spacing is not significant
    assert not gstin_valid("27AAPFU0939F1ZW")             # one wrong check character
    assert not gstin_valid("27AAPFU0939F1Z")              # one character short
    assert gstin_valid(gstin_with_check(33, "AAKCA1234K"))
    assert pan_of_gstin("27AAPFU0939F1ZV") == "AAPFU0939F"
    assert pan_valid("AAKCA1234K") and not pan_valid("AAKXA1234K")     # 'X' is not a kind of holder
    assert cin_valid("U64920MH2004PTC123456") and not cin_valid("U64920MH2004XYZ123456")
    assert ifsc_valid("NRMX0004521") and not ifsc_valid("NRMX1004521")  # the fifth character is always zero


@pytest.mark.parametrize("text, expected", [
    ("Rs. 12,34,56,000/-", (Decimal("123456000"), "INR")),     # lakh and crore grouping
    ("INR 123,456,000", (Decimal("123456000"), "INR")),        # thousands grouping
    ("Rs 25 crore", (Decimal("250000000"), "INR")),
    ("Rs. 12.5 lakh", (Decimal("1250000"), "INR")),
    ("INR 12,34,56,000.50", (Decimal("123456000.50"), "INR")),
    ("Rs. 25,00,00,000/- only", (Decimal("250000000"), "INR")),
    ("Rs. 1,23,4567", None),          # an OCR slip in the grouping: not guessed
    ("Rs. 12,345,67,000", None),      # two groupings mixed
    ("Rs. 5,00,000 plus interest of Rs. 20,000", None),   # two amounts: which one is a question for a person
    ("12,34,56,000", None),           # no currency
])
def test_money_is_parsed_strictly(text, expected):
    assert parse_money(text) == expected


def test_amounts_in_words_are_read_the_indian_way():
    assert rupees_in_words(123456000) == "Rupees Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Only"
    assert rupees_in_words(2500000000) == "Rupees Two Hundred Fifty Crore Only"
    for n in (1005, 100000, 47800000, 123456000, 2500000000, 999999999):
        assert parse_rupee_words(rupees_in_words(n)) == n
    assert parse_rupee_words("Rupees One Lakh Crore Only") is None        # a scale with no number before it
    assert parse_rupee_words("Rupees Five Lakh Two Crore Only") is None   # scales out of order
    assert parse_rupee_words("Rupees Twenty-Two Hundred Only") is None    # not how an Indian form writes it
    assert parse_rupee_words("Rupees Twelve Crore and Fifty Paise Only") is None
    assert format_inr(123456000) == "Rs. 12,34,56,000"


@pytest.mark.parametrize("text, expected", [
    ("20.06.2026", date(2026, 6, 20)), ("20/06/2026", date(2026, 6, 20)), ("20-06-2026", date(2026, 6, 20)),
    ("20-Jun-2026", date(2026, 6, 20)), ("20th June, 2026", date(2026, 6, 20)),
    ("20th day of June, 2026", date(2026, 6, 20)), ("2026-06-20", date(2026, 6, 20)),
    ("31.02.2027", None), ("sometime in 2027", None),
])
def test_dates_are_day_first_and_impossible_dates_rejected(text, expected):
    assert parse_date(text) == expected


def test_labels_match_only_at_the_start_and_on_a_word_boundary():
    assert after_label("Valid until: 31.03.2027", ["valid until"]) == "31.03.2027"
    assert after_label("Earlier authority valid until: 31.03.2026", ["valid until"]) is None   # the decoy label
    assert after_label("Lenders: Anvaya Fincorp Ltd", ["lender"]) is None                     # Lenders is not Lender
    assert after_label("Dateofdefault:14.03.2024", ["date of default"]) == "14.03.2024"       # OCR dropped the spaces
    assert after_label("BANK ACCOUNT CONFIRMATION", ["bank"], require_separator=True) is None
    assert after_label("Bank: Narmada Commercial Bank", ["bank"], require_separator=True) == "Narmada Commercial Bank"


def test_name_similarity_ignores_legal_form_but_not_extra_words():
    assert name_similarity("Anvaya Fincorp Pvt Ltd", "Anvaya Fincorp Private Limited") == 100
    assert name_similarity("AnvayaFincorpPvtLtd", "Anvaya Fincorp Pvt Ltd") == 100          # OCR glued the words
    assert name_similarity("Anvaya Fincorp Pvl Ltd", "Anvaya Fincorp Pvt Ltd") == 100        # OCR misread the suffix
    assert name_similarity("Shree Anvaya Fincorp Ltd", "Anvaya Fincorp Ltd") < 92            # a different company
    assert name_similarity("Anvaya Fincorp Services Ltd", "Anvaya Fincorp Ltd") < 92
    assert name_similarity("Anvaya Fincorp India Ltd", "Anvaya Fincorp Ltd") < 92
