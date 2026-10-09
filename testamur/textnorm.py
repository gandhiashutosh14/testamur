"""Normalisation and strict parsing. OCR drops spaces, mangles separators and invents digit groupings; the parsers
here accept the variants a person would accept and reject anything ambiguous, so a doubtful value becomes a
missing value (and an ABSTAIN downstream) instead of a confident wrong one."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

_NON_ALNUM = re.compile(r"[^0-9a-z]")
_SPACES = re.compile(r"\s+")


def squash(s: str) -> str:
    """Case-folded alphanumerics only: 'Policy  no.' and 'Policyno' compare equal."""
    return _NON_ALNUM.sub("", (s or "").casefold())


_SEPARATOR = re.compile(r"^\s*[:;\-–—=]")


def after_label(line: str, labels: list[str], require_separator: bool = False) -> str | None:
    """If the line starts with one of the labels (ignoring case, spacing and punctuation), return what follows.

    A label must match at the start of the line, so 'Prior period expiry date: ...' does not match 'expiry date'.
    With `require_separator`, the label must be followed by ':' or a dash, so a title such as 'BANK ACCOUNT
    CONFIRMATION' is not read as the value of a 'Bank' field."""
    for label in sorted(labels, key=len, reverse=True):
        target = squash(label)
        built = ""
        for i, ch in enumerate(line):
            c = _NON_ALNUM.sub("", ch.casefold())
            if c:
                built += c
            if len(built) >= len(target):
                if built == target:
                    rest = line[i + 1:]
                    # the label must end at a word boundary, otherwise 'Insured' would match 'Insurer'
                    if rest and rest[0].isalnum() and line[i].isalnum():
                        break
                    if require_separator and not _SEPARATOR.match(rest):
                        break
                    return rest.lstrip(" \t:;-–—=.").strip()
                break
    return None


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_D_MON_Y = re.compile(r"^(\d{1,2})\s*(?:st|nd|rd|th)?\s*([A-Za-z]{3,9})\.?,?\s*(\d{4})$")
_MON_D_Y = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DMY = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$")


def _month(name: str) -> int | None:
    key = name[:3].casefold()
    if key not in _MONTHS:
        return None
    full = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
            "november", "december"][_MONTHS[key] - 1]
    n = name.casefold().rstrip(".")
    return _MONTHS[key] if (full.startswith(n) or n == "sept") else None


def parse_date(text: str) -> date | None:
    """Day-first dates in the usual written forms. Returns None for anything ambiguous or impossible."""
    s = _SPACES.sub(" ", (text or "").strip()).rstrip(".")
    s = re.split(r"\s+(?:at|to|until)\s+", s)[0]
    for rx, order in ((_D_MON_Y, "dmy"), (_MON_D_Y, "mdy")):
        m = rx.match(s)
        if m:
            a, b, y = m.groups()
            day, mon = (int(a), _month(b)) if order == "dmy" else (int(b), _month(a))
            if mon is None:
                return None
            try:
                return date(int(y), mon, day)
            except ValueError:
                return None
    m = _ISO.match(s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = _DMY.match(s)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


_CURRENCY = {"eur": "EUR", "€": "EUR", "gbp": "GBP", "£": "GBP", "usd": "USD", "$": "USD"}
_MONEY = re.compile(
    r"^(?P<c1>EUR|GBP|USD|€|£|\$)?\s*(?P<num>\d[\d.,' ]*?)\s*"
    r"(?P<mult>m|mn|million|k|thousand)?\s*(?P<c2>EUR|GBP|USD|€|£|\$)?$", re.I)
_GROUPED = re.compile(r"^\d{1,3}(?:(?P<sep>[,.' ])\d{3})*$")


def parse_number(num: str) -> Decimal | None:
    """Digits with optional thousands grouping and an optional two-digit decimal part. One grouping separator,
    used consistently, with groups of exactly three digits; anything else is rejected rather than guessed."""
    num = num.strip()
    frac = ""
    m = re.match(r"^(.*\d)([.,])(\d{2})$", num)
    if m and not _GROUPED.match(num):           # '1,250.50' or '1.250,50' or '12,50'
        num, dec_sep, frac = m.groups()
        if dec_sep in num:
            return None
    if not num.isdigit():
        g = _GROUPED.match(num)
        if not g:
            return None
        if len({c for c in num if not c.isdigit()}) != 1:
            return None
        num = re.sub(r"[,.' ]", "", num)
    try:
        return Decimal(f"{num}.{frac}" if frac else num)
    except InvalidOperation:
        return None


_CURRENCY_MARK = re.compile(r"(?<![a-z])(?:eur|gbp|usd)(?![a-z])|[€£$]", re.I)
_DIGIT_RUN = re.compile(r"\d[\d.,' ]*\d|\d")


def parse_money(text: str) -> tuple[Decimal, str] | None:
    """'EUR 5,000,000', '€5m', '5.000.000 EUR'. Returns (amount, currency) or None if the grouping is malformed
    (OCR often reads '5,000,000' as '5,000000'; that must not become five thousand), the currency is unknown, or the
    text states more than one amount (an aggregate beside a per-occurrence limit, a limit beside an excess): which of
    them the rule should compare is a question for a person, not the first number on the line."""
    s = _SPACES.sub(" ", (text or "").strip())
    big = sum(len(re.sub(r"\D", "", run)) >= 4 for run in _DIGIT_RUN.findall(s))
    if len(_CURRENCY_MARK.findall(s)) > 1 or big > 1:
        return None
    s = re.split(r"\s+(?:any|each|per|in the|aggregate)\b", s, flags=re.I)[0].strip()
    m = _MONEY.match(s)
    if not m:
        return None
    cur = (m["c1"] or m["c2"] or "").casefold()
    if cur not in _CURRENCY:
        return None
    amount = parse_number(m["num"])
    if amount is None:
        return None
    mult = (m["mult"] or "").casefold()
    if mult in ("m", "mn", "million"):
        amount *= 1_000_000
    elif mult in ("k", "thousand"):
        amount *= 1_000
    return amount, _CURRENCY[cur]


_LEGAL_SUFFIXES = {"ltd", "limited", "plc", "llp", "gmbh", "ag", "sa", "sas", "srl", "bv", "nv", "inc", "llc",
                   "corp", "corporation", "co", "company", "the"}
# How OCR misreads legal-form suffixes on scans and photos ('plc' read as 'ple', 'Ltd' as '1td'). Only applied to
# the last token, where a legal form sits, so a real word is never rewritten.
_OCR_SUFFIX = {"ple": "plc", "pic": "plc", "p1c": "plc", "plo": "plc", "1td": "ltd", "itd": "ltd", "lid": "ltd",
               "ltd1": "ltd", "gmbn": "gmbh", "gmhh": "gmbh", "8v": "bv"}


def normalize_name(name: str) -> str:
    s = (name or "").casefold().replace("&", " and ")
    tokens = re.sub(r"[^0-9a-z ]", " ", s).split()
    if tokens and tokens[-1] in _OCR_SUFFIX:
        tokens[-1] = _OCR_SUFFIX[tokens[-1]]
    return " ".join(t for t in tokens if t not in _LEGAL_SUFFIXES)


_GLUED_SUFFIX = re.compile(r"(?:limited|ltd|plc|llp|gmbh|llc|inc|corp)$")


def _squashed_name(normalized: str) -> str:
    s = normalized.replace(" ", "")
    return _GLUED_SUFFIX.sub("", s) if len(s) > 8 else s     # 'brightwaterlogisticsltd': OCR glued the suffix on


def name_similarity(a: str, b: str) -> float:
    """0-100. Ignores case, punctuation and legal-form suffixes; tolerant to OCR dropping the spaces between words.
    Deliberately not token-set based: 'Harbor Freight' must not score 100 against 'Harbor Freight Holdings'."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.ratio(na, nb), fuzz.ratio(_squashed_name(na), _squashed_name(nb)))
