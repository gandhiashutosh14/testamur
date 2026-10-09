"""Identifiers with check digits. Validating them is free, deterministic and decisive, which is why matching tries
them before any fuzzy comparison.

- LEI (ISO 17442): 20 alphanumeric characters; the last two are ISO 7064 MOD 97-10 check digits.
- IBAN (ISO 13616): country code, two check digits, then the national account number; the same MOD 97-10 scheme
  applied to the rearranged string.
"""
from __future__ import annotations

import re

_ALNUM = re.compile(r"[^0-9A-Za-z]")


def compact(value: str) -> str:
    return _ALNUM.sub("", value or "").upper()


def _as_digits(s: str) -> str:
    return "".join(str(int(ch, 36)) for ch in s)


def mod97(s: str) -> int:
    return int(_as_digits(s)) % 97


def lei_valid(value: str) -> bool:
    s = compact(value)
    return len(s) == 20 and s.isalnum() and s[18:].isdigit() and mod97(s) == 1


def lei_with_check(prefix18: str) -> str:
    p = compact(prefix18)
    assert len(p) == 18, p
    return p + f"{98 - mod97(p + '00'):02d}"


def iban_valid(value: str) -> bool:
    s = compact(value)
    if not (15 <= len(s) <= 34) or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    return mod97(s[4:] + s[:4]) == 1


def iban_with_check(country: str, bban: str) -> str:
    c, b = country.upper(), compact(bban)
    return f"{c}{98 - mod97(b + c + '00'):02d}{b}"


def format_iban(value: str) -> str:
    s = compact(value)
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


_COMPANY_NO = re.compile(r"^(?:[A-Z]{2}\d{6}|\d{8})$")
_VAT = re.compile(r"^[A-Z]{2}\d{8,12}$")


def company_number_valid(value: str) -> bool:
    return bool(_COMPANY_NO.match(compact(value)))


def vat_valid(value: str) -> bool:
    return bool(_VAT.match(compact(value)))
