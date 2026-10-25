"""Ground-truth metrics, each the one its dataset's authors use or the plainest stand-in for it.

- extract (CUAD): an answer is right when its words overlap a gold clause with Jaccard >= 0.5, the CUAD paper's
  criterion; where the contract has no such clause, abstaining is right.
- nli (ContractNLI) and yesno (PubMedQA): the label.
- answer: TAT-QA numbers within 0.5% (sign kept), spans by token F1 >= 0.8; QASPER by token F1 >= 0.5 against any
  annotator's answer, yes/no by the word; where the paper does not answer, abstaining is right.
- retrieval: a passage holds the evidence when a gold span's words are in it (all of a short one, 60% of a long
  one); for PubMedQA, the pool of abstracts, when it is the question's own abstract.
"""
from __future__ import annotations

import re
import string
from collections import Counter

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)
_NUMBER = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?\)?")
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


def numbers(text: str) -> list[float]:
    """The numbers in a text, with accounting brackets as negatives and a following scale word applied."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        raw = m.group(0)
        neg = raw.startswith("(") and raw.endswith(")") or "-" in raw
        try:
            v = float(raw.strip("()$").replace(",", "").replace("$", "").lstrip("-"))
        except ValueError:
            continue
        after = text[m.end():m.end() + 12].lower()
        for word, factor in (("billion", 1e9), ("million", 1e6), ("thousand", 1e3)):
            if after.strip().startswith(word):
                v *= factor
        out.append(-v if neg else v)
    return out


def number_matches(pred: str, gold: float, scale: str) -> bool:
    candidates = {gold, gold * SCALES.get(scale, 1.0)}
    for p in numbers(pred):
        for c in candidates:
            if abs(p - c) <= 0.005 * max(abs(c), 1e-9):
                return True
    return False


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
        return token_f1(answer, gold) >= 0.8
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
