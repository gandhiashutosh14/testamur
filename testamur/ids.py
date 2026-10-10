"""Identifiers, and the ones with check characters. Validating them is free, deterministic and decisive, which is why
matching tries them before any fuzzy comparison.

- LEI (ISO 17442): 20 alphanumeric characters; the last two are ISO 7064 MOD 97-10 check digits.
- GSTIN: 15 characters. Two digits of state code, the holder's 10-character PAN, an entity number, a default 'Z',
  then a check character computed over the first 14 with the Luhn mod 36 algorithm.
- PAN: 10 characters, five letters, four digits, a letter. The fourth letter says what kind of holder it is ('C' for
  a company); the last one is a check character whose algorithm is not published, so only the structure is checked.
- CIN: 21 characters. Listed or unlisted, the industry code, the state, the year of incorporation, the kind of
  company (public, private, ...) and a registration number. Structure only; it carries no check character.
- IFSC: 11 characters. The bank's four-letter code, a zero, then six characters naming the branch. No check
  character either, but the bank code must be the code of the bank the letter names.
"""
from __future__ import annotations

import re

_ALNUM = re.compile(r"[^0-9A-Za-z]")
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


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


_PAN = re.compile(r"^[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]$")
_GSTIN = re.compile(r"^\d{2}[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CIN = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}(?:PLC|PTC|FLC|FTC|GOI|GAP|GAT|NPL|ULL|ULT|SGC)\d{6}$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def gstin_check_char(first14: str) -> str:
    """Luhn mod 36 over the first 14 characters, the factor alternating 1, 2 from the left."""
    total = 0
    for i, ch in enumerate(compact(first14)):
        product = _BASE36.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _BASE36[(36 - total % 36) % 36]


def gstin_valid(value: str) -> bool:
    s = compact(value)
    return bool(_GSTIN.match(s)) and gstin_check_char(s[:14]) == s[14]


def gstin_with_check(state_code: int, pan: str, entity: str = "1") -> str:
    first14 = f"{state_code:02d}{compact(pan)}{entity}Z"
    return first14 + gstin_check_char(first14)


def pan_of_gstin(value: str) -> str | None:
    s = compact(value)
    return s[2:12] if len(s) == 15 else None


def pan_valid(value: str) -> bool:
    return bool(_PAN.match(compact(value)))


def cin_valid(value: str) -> bool:
    return bool(_CIN.match(compact(value)))


def ifsc_valid(value: str) -> bool:
    return bool(_IFSC.match(compact(value)))
