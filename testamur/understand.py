"""From pages of text to a typed, attributed document: what kind of document it is, the field values it states (with
the page and line each came from), the identifiers on it, and any text aimed at automated readers.

Deterministic first. Classification uses the title and the labels a document type always carries; the filename is a
weak signal because filenames lie. A model is consulted only when the deterministic signals are ambiguous, and its
answer is accepted only above a confidence threshold. Field values that cannot be parsed, or that the document
states twice with different values, are reported as missing or conflicting, never guessed."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .ids import compact, company_number_valid, iban_valid, lei_valid, vat_valid
from .model import Extraction, FieldValue
from .pack import DocTypeSpec, Pack
from .textnorm import after_label, parse_date, parse_money, squash

CLASS_ACCEPT = 0.55
CLASS_MARGIN = 0.2


@dataclass
class Classification:
    doc_type: str | None
    score: float
    method: str                      # rules | model | none
    scores: dict[str, float] = field(default_factory=dict)


def classify(pack: Pack, filename: str, ex: Extraction, model_fallback=None) -> Classification:
    first_page = ex.pages[0].lines[:6] if ex.pages else []
    title = squash(" ".join(line.text for line in first_page))
    all_lines = [line.text for p in ex.pages for line in p.lines]
    fname = squash(filename)
    scores: dict[str, float] = {}
    for name, spec in pack.doc_types.items():
        s = 0.0
        if any(squash(k) in title for k in spec.title_keywords):
            s += 0.6
        if spec.key_labels:
            found = sum(1 for lab in spec.key_labels if any(_starts_with_label(t, lab) for t in all_lines))
            s += 0.4 * found / len(spec.key_labels)
        if any(squash(a) in fname for a in spec.filename_aliases):
            s += 0.1
        scores[name] = round(s, 3)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, best_s), (_, second_s) = ranked[0], ranked[1]
    if best_s >= CLASS_ACCEPT and best_s - second_s >= CLASS_MARGIN:
        return Classification(best, best_s, "rules", scores)
    if model_fallback is not None:
        answer = model_fallback(list(pack.doc_types), ex)
        if answer is not None:
            doc_type, confidence = answer
            if doc_type in pack.doc_types and confidence >= 0.8:
                return Classification(doc_type, confidence, "model", scores)
    return Classification(None, best_s, "none", scores)


def _starts_with_label(line: str, label: str) -> bool:
    return squash(line).startswith(squash(label))


@dataclass
class Fields:
    values: dict[str, FieldValue] = field(default_factory=dict)
    conflicts: dict[str, list[FieldValue]] = field(default_factory=dict)
    unparsed: dict[str, list[str]] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        fv = self.values.get(name)
        return fv.value if fv else None


def _parse(kind: str, raw: str):
    if kind == "money":
        return parse_money(raw)
    if kind == "date":
        return parse_date(raw)
    if kind == "list":
        items = [x.strip() for x in re.split(r"[;\n]", raw) if x.strip()]
        return items or None
    return raw.strip() or None


def read_fields(spec: DocTypeSpec, ex: Extraction) -> Fields:
    out = Fields()
    for name, fs in spec.fields.items():
        found: list[FieldValue] = []
        for page in ex.pages:
            lines = page.lines
            for i, line in enumerate(lines):
                rest = after_label(line.text, fs.labels, require_separator=(fs.type == "text"))
                if rest is None:
                    continue
                if not rest and i + 1 < len(lines):          # the value sits on the line below its label
                    rest = lines[i + 1].text
                value = _parse(fs.type, rest) if rest else None
                if value is None:
                    if rest:
                        out.unparsed.setdefault(name, []).append(rest)
                    continue
                found.append(FieldValue(name=name, value=value, raw=rest, page=page.number, line=line.text,
                                        conf=line.conf))
        distinct = {_key(f.value): f for f in found}
        if len(distinct) == 1:
            out.values[name] = found[0]
        elif len(distinct) > 1:
            out.conflicts[name] = list(distinct.values())
    return out


def _key(value) -> str:
    if isinstance(value, tuple):
        return f"{value[0]}|{value[1]}"
    if isinstance(value, list):
        return "|".join(squash(v) for v in value)
    return squash(str(value))


_LEI = re.compile(r"\b[0-9A-Z]{18}\d{2}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[0-9A-Z]{4}){2,7}(?: ?[0-9A-Z]{1,4})?\b")
_COMPANY = re.compile(r"company (?:number|no\.?|registration number)[:\s]*([A-Z]{2}\d{6}|\d{8})", re.I)
_VAT = re.compile(r"\bvat(?: registration)? number[:\s]*([A-Z]{2}\s?\d[\d ]{7,13})", re.I)


def identifiers(ex: Extraction) -> dict[str, list[str]]:
    """Identifiers that pass their own validation. An identifier with bad check digits is not evidence of anything."""
    text = ex.text()
    upper = text.upper()
    found: dict[str, set[str]] = {"lei": set(), "iban": set(), "company_number": set(), "vat_number": set()}
    for m in _LEI.finditer(upper):
        if lei_valid(m.group()):
            found["lei"].add(m.group())
    for m in _IBAN.finditer(upper):
        if iban_valid(m.group()):
            found["iban"].add(compact(m.group()))
    for m in _COMPANY.finditer(text):
        if company_number_valid(m.group(1)):
            found["company_number"].add(compact(m.group(1)))
    for m in _VAT.finditer(text):
        if vat_valid(m.group(1)):
            found["vat_number"].add(compact(m.group(1)))
    return {k: sorted(v) for k, v in found.items() if v}


def hostile_spans(pack: Pack, ex: Extraction) -> list[str]:
    """Text that addresses an automated reader. Documents are untrusted input."""
    text = " ".join(line.text for p in ex.pages for line in p.lines)
    hits = []
    for pat in pack.injection_patterns():
        for m in pat.finditer(text):
            hits.append(text[max(0, m.start() - 60):m.end() + 60])
    return hits
