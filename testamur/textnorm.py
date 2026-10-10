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
                    # the label must end at a word boundary, otherwise 'Lender' would match 'Lenders'
                    if rest and rest[0].isalnum() and line[i].isalnum():
                        break
                    if require_separator and not _SEPARATOR.match(rest):
                        break
                    return rest.lstrip(" \t:;-–—=.").strip()
                break
    return None



_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_D_MON_Y = re.compile(r"^(\d{1,2})\s*(?:st|nd|rd|th)?(?:\s*day of)?[\s-]*([A-Za-z]{3,9})\.?,?[\s-]*(\d{4})$")
_MON_D_Y = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DMY = re.compile(r"^(\d{1,2})([/.-])(\d{1,2})\2(\d{4})$")


def _month(name: str) -> int | None:
    key = name[:3].casefold()
    if key not in _MONTHS:
        return None
    full = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
            "november", "december"][_MONTHS[key] - 1]
    n = name.casefold().rstrip(".")
    return _MONTHS[key] if (full.startswith(n) or n == "sept") else None


def parse_date(text: str) -> date | None:
    """Day-first dates in the forms Indian documents use: '20.06.2026', '20/06/2026', '20-06-2026', '20-Jun-2026',
    '20th June, 2026', '20th day of June, 2026'. Returns None for anything ambiguous or impossible."""
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
            return date(int(m[4]), int(m[3]), int(m[1]))
        except ValueError:
            return None
    return None


_MONEY = re.compile(
    r"^(?P<c1>INR|Rs\.?|₹)?\s*(?P<num>\d[\d.,]*?)\s*"
    r"(?P<mult>crores?|cr\.?|lakhs?|lacs?)?\s*(?P<c2>INR|Rs\.?|₹)?\s*(?:/-)?\s*(?:only)?$", re.I)
_GROUPED = re.compile(r"^\d{1,3}(?:,\d{3})*$")            # 123,456,789
_GROUPED_IN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")   # 12,34,56,789: lakh and crore grouping
_MULTIPLIER = {"crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "cr.": 10_000_000,
               "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000}


def parse_number(num: str) -> Decimal | None:
    """Digits with optional grouping and an optional two-digit decimal part. Grouping must be one of the two a person
    writes, used consistently: thousands (123,456,789) or lakh and crore (12,34,56,789). Anything else ('12,3456',
    '1,23,4567') is rejected rather than guessed: OCR invents groupings, and a wrong one moves a decimal point."""
    num = num.strip()
    frac = ""
    m = re.match(r"^(.*\d)\.(\d{2})$", num)
    if m:
        num, frac = m.groups()
    if "." in num:
        return None
    if not num.isdigit() and not (_GROUPED.match(num) or _GROUPED_IN.match(num)):
        return None
    try:
        return Decimal(f"{num.replace(',', '')}.{frac}" if frac else num.replace(",", ""))
    except InvalidOperation:
        return None


_CURRENCY_MARK = re.compile(r"(?<![a-z])(?:inr|rs)(?![a-z])|₹", re.I)
_DIGIT_RUN = re.compile(r"\d[\d.,]*\d|\d")


def parse_money(text: str) -> tuple[Decimal, str] | None:
    """'Rs. 12,34,56,000/-', 'INR 123,456,000', 'Rs 25 crore', 'Rs. 12.5 lakh'. Returns (amount, 'INR') or None if the
    grouping is malformed, there is no currency, or the text states more than one amount (a principal beside the
    interest on it): which of them a rule should compare is a question for a person, not the first number on the
    line."""
    s = _SPACES.sub(" ", (text or "").strip())
    big = sum(len(re.sub(r"\D", "", run)) >= 4 for run in _DIGIT_RUN.findall(s))
    if len(_CURRENCY_MARK.findall(s)) > 1 or big > 1:
        return None
    s = re.split(r"\s+(?:plus|towards|being|for|as on|as at)\b", s, flags=re.I)[0].strip()
    m = _MONEY.match(s)
    if not m or not (m["c1"] or m["c2"]):
        return None
    mult = (m["mult"] or "").casefold()
    if mult:
        if not re.match(r"^\d{1,4}(?:\.\d{1,2})?$", m["num"]):
            return None
        return Decimal(m["num"]) * _MULTIPLIER[mult], "INR"
    amount = parse_number(m["num"])
    return (amount, "INR") if amount is not None else None


def format_inr(amount) -> str:
    """'Rs. 12,34,56,000': lakh and crore grouping, as Indian documents write it."""
    whole = int(amount)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"Rs. {s}"


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_WORD_VALUE = {w: i for i, w in enumerate(_ONES)} | {w: 10 * i for i, w in enumerate(_TENS) if w}
_SCALES = {"crore": 10_000_000, "crores": 10_000_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
           "lacs": 100_000, "thousand": 1_000}


def _below_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words.append(f"{_ONES[n // 100].title()} Hundred")
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10].title() + (f"-{_ONES[n % 10].title()}" if n % 10 else ""))
    elif n:
        words.append(_ONES[n].title())
    return " ".join(words)


def rupees_in_words(amount) -> str:
    """'Rupees Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Only': how an Indian form states an amount in words."""
    n = int(amount)
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    parts = [f"{_below_thousand(v)} {unit}" for v, unit in ((crore, "Crore"), (lakh, "Lakh"), (thousand, "Thousand"))
             if v]
    if n:
        parts.append(_below_thousand(n))
    return "Rupees " + (" ".join(parts) if parts else "Zero") + " Only"


def parse_rupee_words(text: str) -> Decimal | None:
    """The amount an Indian form states in words. Scales must come in descending order (crore, lakh, thousand) and every
    word must be a number word; anything else, paise included, is not read rather than half-read."""
    s = re.sub(r"[-,]", " ", (text or "").casefold().replace("&", " and "))
    tokens = [t for t in re.sub(r"[^a-z ]", " ", s).split() if t not in ("and",)]
    while tokens and tokens[0] in ("rupees", "rupee", "rs", "inr", "indian"):
        tokens = tokens[1:]
    while tokens and tokens[-1] in ("only",):
        tokens = tokens[:-1]
    if not tokens:
        return None
    total, current, last_scale, seen = 0, 0, None, False
    for t in tokens:
        if t in _WORD_VALUE:
            current += _WORD_VALUE[t]
            seen = True
        elif t == "hundred":
            if not 0 < current < 10:            # 'Nine Hundred', never 'Twenty-Two Hundred'
                return None
            current *= 100
        elif t in _SCALES:
            scale = _SCALES[t]
            if current == 0 or (last_scale is not None and scale >= last_scale):
                return None
            total += current * scale
            current, last_scale = 0, scale
        else:
            return None
    if not seen:
        return None
    return Decimal(total + current)


# Legal-form words, ignored when comparing names. 'Pvt', 'Private' and the '(P)' of '(P) Ltd' are forms too; 'India',
# 'Shree' and 'Services' are not: 'Shree Anvaya Fincorp Ltd' and 'Anvaya Fincorp Ltd' are different companies.
_LEGAL_SUFFIXES = {"ltd", "limited", "pvt", "private", "p", "llp", "inc", "llc", "corp", "corporation", "co",
                   "company", "the"}
# How OCR misreads legal-form suffixes on scans and photos ('Ltd' as '1td', 'Pvt' as 'Pvl'). Only applied to the last
# two tokens, where a legal form sits, so a real word is never rewritten.
_OCR_SUFFIX = {"1td": "ltd", "itd": "ltd", "lid": "ltd", "ltd1": "ltd", "pvl": "pvt", "pyt": "pvt", "pvi": "pvt",
               "limlted": "limited", "llmited": "limited", "11p": "llp", "lip": "llp"}


def normalize_name(name: str) -> str:
    s = (name or "").casefold().replace("&", " and ")
    tokens = re.sub(r"[^0-9a-z ]", " ", s).split()
    for i in range(max(0, len(tokens) - 2), len(tokens)):
        tokens[i] = _OCR_SUFFIX.get(tokens[i], tokens[i])
    return " ".join(t for t in tokens if t not in _LEGAL_SUFFIXES)


_GLUED_SUFFIX = re.compile(r"(?:pvtltd|privatelimited|limited|ltd|llp)$")


def _squashed_name(normalized: str) -> str:
    s = normalized.replace(" ", "")
    return _GLUED_SUFFIX.sub("", s) if len(s) > 8 else s     # 'anvayafincorppvtltd': OCR glued the suffix on


def name_similarity(a: str, b: str) -> float:
    """0-100. Ignores case, punctuation and legal-form suffixes; tolerant to OCR dropping the spaces between words.
    Deliberately not token-set based: 'Anvaya Fincorp' must not score 100 against 'Anvaya Fincorp Services'."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.ratio(na, nb), fuzz.ratio(_squashed_name(na), _squashed_name(nb)))
