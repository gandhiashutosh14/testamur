"""Ground-truth metrics, each the one its dataset's authors use or the plainest stand-in for it.

- extract (CUAD): an answer is right when its words overlap a gold clause with Jaccard >= 0.5, the CUAD paper's
  criterion; where the contract has no such clause, abstaining is right.
- nli (ContractNLI) and yesno (PubMedQA): the label.
- answer: TAT-QA's numbers as numbers, within 0.5%, with their sign and in their unit (a computed answer by the
  first number the answer states, years aside; a gold span that is a number by every number the answer states), its
  text spans by token F1 >= 0.8; QASPER by token F1 >= 0.5 against any annotator's answer, yes/no by the word; where
  the paper does not answer, abstaining is right.
- retrieval: a passage holds the evidence when a gold span's words are in it (all of a short one, 60% of a long
  one); for PubMedQA, the pool of abstracts, when it is the question's own abstract.
"""
from __future__ import annotations

import re
import string
from collections import Counter
from dataclasses import dataclass

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)
# a number on its own (not the 4 of "Q4" or the 2019 of "FY2019"), with accounting brackets, a sign, a dollar sign
# and a unit after it: a per cent sign or word, a scale word or its abbreviation, or a currency named in words
_NUMBER = re.compile(r"""
    (?<![\w.,])
    (?P<open>\()? (?P<sign>[-−])? (?P<cur>\$)? (?P<sign2>[-−])?
    (?P<num>\d[\d,]*(?:\.\d+)?)
    (?P<close>\))?
    (?:\s*(?P<unit>%|per\s?cent\b|thousand\b|million\b|billion\b|mn\b|bn\b|[kmb]\b
                  |dollars?\b|usd\b|euros?\b|pounds?\b|rupees?\b))?
    """, re.I | re.X)
_FACTORS = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9}
# a currency named after the number with no scale word ("5 dollars") states the amount in units: a scale of one
_UNITS = {"dollar", "dollars", "usd", "euro", "euros", "pound", "pounds", "rupee", "rupees"}
_YEAR = re.compile(r"(?:19|20)\d\d")
SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "percent": 1.0, "": 1.0}


def normalize(s: str) -> str:
    """SQuAD's normalisation: lower case, no punctuation, no articles, single spaces."""
    s = (s or "").lower().translate(_PUNCT)
    return " ".join(_ARTICLES.sub(" ", s).split())


def tokens(s: str) -> list[str]:
    return normalize(s).split()


def token_f1(pred: str, gold: str) -> float:
    p, g = tokens(pred), tokens(gold)
    if not p or not g:
        return float(p == g)
    common = sum((Counter(p) & Counter(g)).values())
    if not common:
        return 0.0
    precision, recall = common / len(p), common / len(g)
    return 2 * precision * recall / (precision + recall)


def jaccard(pred: str, gold: str) -> float:
    p, g = set(tokens(pred)), set(tokens(gold))
    return len(p & g) / len(p | g) if p | g else 1.0


@dataclass(frozen=True)
class Stated:
    """A number as a text states it: its value (signed, a scale word not applied), and its unit."""
    value: float
    percent: bool = False
    factor: float | None = None          # the scale word's, when there is one
    currency: bool = False
    year: bool = False                   # a bare four-digit 19xx or 20xx


def stated(text: str) -> list[Stated]:
    out = []
    for m in _NUMBER.finditer(text or ""):
        try:
            v = float(m["num"].replace(",", ""))
        except ValueError:
            continue
        neg = bool(m["sign"] or m["sign2"] or (m["open"] and m["close"]))
        unit = (m["unit"] or "").lower().replace(" ", "")
        percent = unit in ("%", "percent")
        factor = 1.0 if unit in _UNITS else _FACTORS.get(unit)
        bare = not (neg or m["cur"] or unit or m["open"])
        out.append(Stated(-v if neg else v, percent, factor, bool(m["cur"]) or unit in _UNITS,
                          bare and _YEAR.fullmatch(m["num"]) is not None))
    return out


def numbers(text: str) -> list[float]:
    """The numbers in a text, with accounting brackets as negatives and a following scale word applied."""
    return [s.value * (s.factor or 1.0) for s in stated(text)]


def number_matches(pred: str, gold: float, scale: str) -> bool:
    """Whether an answer states the gold number. It must state one quantity, years aside: an answer that states two
    (a result and a figure it worked from, a value it denies and one it asserts) is not taken to assert either. That
    quantity must equal the gold within 0.5%, with its sign, in the gold's unit: a percentage stated as one or bare,
    never as money or with a scale word; an amount never as a percentage, at its scale word's scale when it has one
    (a currency named in words, "5 dollars", is a scale of one), else at the table's scale or written out in full."""
    said = [s for s in stated(pred) if not s.year]
    if not said or any(not _one_quantity(s, said[0]) for s in said[1:]):
        return False
    return _same(said[0], Stated(gold, percent=scale == "percent"), scale)


def _one_quantity(a: Stated, b: Stated) -> bool:
    return a.percent == b.percent and _close(a.value * (a.factor or 1.0), b.value * (b.factor or 1.0))


def _close(v: float, g: float) -> bool:
    return abs(v - g) <= 0.005 * max(abs(g), 1e-9)


def _same(said: Stated, gold: Stated, scale: str) -> bool:
    """A stated number against a gold one, in the gold's unit (the table's `scale` unless the gold names its own)."""
    if scale == "percent" or gold.percent:
        return not said.currency and said.factor is None and _close(said.value, gold.value)
    if said.percent:
        return False
    full = gold.value * (gold.factor or SCALES.get(scale, 1.0))
    if said.factor is not None:
        return _close(said.value * said.factor, full)
    return (gold.factor is None and _close(said.value, gold.value)) or _close(said.value, full)


def _gold_number(span: str) -> Stated | None:
    """A gold span that is one number and nothing else (its unit aside)."""
    found = stated(span)
    if len(found) == 1 and not re.search(r"[a-z]", _NUMBER.sub(" ", span), re.I):
        return found[0]
    return None


def spans_match(pred: str, spans: list[str], scale: str) -> bool | None:
    """Gold spans that are numbers, compared as numbers: the answer states each, with its sign and in its unit, and no
    other number (years count only where the gold has one). None when a span is not a number: then its words decide."""
    gold = [_gold_number(s) for s in spans]
    if not gold or any(g is None for g in gold):
        return None
    years = any(g.year for g in gold)
    left = [s for s in stated(pred) if years or not s.year]
    if len(left) != len(gold):
        return False
    for g in gold:
        hit = next((s for s in left if _same(s, g, scale)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def _as_number(s: str) -> float | None:
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def correct(kind: str, dataset: str, answerable: bool, gold_answers: list[str], gold_label: str | None, meta: dict,
            answer: str, label: str | None, abstained: bool) -> bool:
    if kind in ("nli", "yesno"):
        return label is not None and label == gold_label
    if not answerable:
        return abstained
    if abstained:
        return False
    if kind == "extract":
        return max((jaccard(answer, g) for g in gold_answers), default=0.0) >= 0.5
    if dataset == "tatqa":
        gold = gold_answers[0]
        if meta.get("answer_type") in ("arithmetic", "count") and _as_number(gold) is not None:
            return number_matches(answer, float(gold.replace(",", "")), meta.get("scale", ""))
        numeric = spans_match(answer, meta.get("spans") or [gold], meta.get("scale", ""))
        return numeric if numeric is not None else token_f1(answer, gold) >= 0.8
    if all(g in ("yes", "no") for g in gold_answers):
        first = (tokens(answer) or [""])[0]
        return first in gold_answers
    return max((token_f1(answer, g) for g in gold_answers), default=0.0) >= 0.5


def answer_f1(answer: str, gold_answers: list[str]) -> float:
    return max((token_f1(answer, g) for g in gold_answers), default=0.0)


def holds(passage_text: str, evidence: str) -> bool:
    """Whether a passage holds a piece of gold evidence: all of a short span's words, 60% of a long one's."""
    e = tokens(evidence)
    if not e:
        return False
    if normalize(evidence) in normalize(passage_text):
        return True
    have = Counter(tokens(passage_text))
    found = sum(min(n, have[w]) for w, n in Counter(e).items())
    return found / len(e) >= (1.0 if len(e) <= 12 else 0.6)


def rank_of_evidence(ranked_texts: list[str], evidence: list[str]) -> int | None:
    """1-based rank of the first passage holding any gold evidence, or None."""
    for i, text in enumerate(ranked_texts, 1):
        if any(holds(text, e) for e in evidence):
            return i
    return None
