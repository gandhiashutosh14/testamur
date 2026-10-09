from datetime import date
from decimal import Decimal

import pytest

from testamur.ids import company_number_valid, iban_valid, iban_with_check, lei_valid, lei_with_check, vat_valid
from testamur.textnorm import after_label, name_similarity, parse_date, parse_money


def test_check_digit_identifiers_validate_and_reject_single_digit_errors():
    lei = lei_with_check("5493001KJTIIGC8Y1R")
    assert lei_valid(lei)
    assert not lei_valid(lei[:-1] + ("0" if lei[-1] != "0" else "1"))
    assert iban_valid("GB33BUKB20201555555555")          # the published example
    assert iban_valid("GB33 BUKB 2020 1555 5555 55")      # spacing is not significant
    assert not iban_valid("GB34BUKB20201555555555")
    assert iban_valid(iban_with_check("GB", "NMBK12345678901234"))
    assert company_number_valid("12345678") and not company_number_valid("1234")
    assert vat_valid("GB123456789") and not vat_valid("GB1234")


@pytest.mark.parametrize("text, expected", [
    ("EUR 5,000,000", (Decimal("5000000"), "EUR")),
    ("€5m", (Decimal("5000000"), "EUR")),
    ("5.000.000 EUR", (Decimal("5000000"), "EUR")),
    ("GBP 2,500,000.00", (Decimal("2500000.00"), "GBP")),
    ("EUR 10000000 any one occurrence", (Decimal("10000000"), "EUR")),
    ("EUR 5 000 000", (Decimal("5000000"), "EUR")),
    ("EUR 5,000000", None),          # an OCR slip: must not become five thousand
    ("EUR 5,000.000", None),         # mixed separators
    ("EUR 5,00,000", None),
    ("5,000,000", None),             # no currency, no amount
])
def test_money_is_parsed_strictly(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("28 February 2027", date(2027, 2, 28)), ("28February2027", date(2027, 2, 28)), ("2027-02-28", date(2027, 2, 28)),
    ("28/02/2027", date(2027, 2, 28)), ("February 28, 2027", date(2027, 2, 28)), ("1st March 2026", date(2026, 3, 1)),
    ("31 Feb 2027", None), ("sometime in 2027", None),
])
def test_dates_are_day_first_and_impossible_dates_rejected(text, expected):
    assert parse_date(text) == expected


def test_labels_match_only_at_the_start_and_on_a_word_boundary():
    assert after_label("Expiry date: 28 February 2027", ["expiry date"]) == "28 February 2027"
    assert after_label("Prior period expiry date: 01 Jan 2026", ["expiry date"]) is None   # the decoy label
    assert after_label("Insurer: Harbourline Mutual", ["insured"]) is None                  # Insurer is not Insured
    assert after_label("Policynumber:HM-1", ["policy number"]) == "HM-1"                    # OCR dropped the space
    assert after_label("BANK ACCOUNT CONFIRMATION", ["bank"], require_separator=True) is None
    assert after_label("Bank: Calder Commercial Bank", ["bank"], require_separator=True) == "Calder Commercial Bank"


def test_name_similarity_ignores_legal_form_but_not_extra_words():
    assert name_similarity("Brightwater Logistics Ltd", "Brightwater Logistics Limited") == 100
    assert name_similarity("BrightwaterLogisticsLtd", "Brightwater Logistics Ltd") == 100    # OCR glued the words
    assert name_similarity("Vantage Holdings ple", "Vantage Holdings plc") == 100            # OCR misread the suffix
    assert name_similarity("Purple Logistics Ltd", "Pur Logistics Ltd") < 92                 # only the suffix slot
    assert name_similarity("Harbor Freight", "Harbor Freight Holdings") < 92                 # parent is not the subsidiary
    assert name_similarity("Northgate Cleaning Ltd", "Northgate Cleaning Services Ltd") < 92
