"""Deterministic check rules, for the metadata and field tiers.

Every rule returns PASS, FAIL or ABSTAIN with the evidence it used. A missing, unparseable or self-contradicting
value is an ABSTAIN, never a PASS: the engine does not round up."""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .ids import compact, iban_valid, lei_valid, vat_valid
from .model import CheckResult, Evidence, FieldValue, Verdict
from .pack import CheckSpec, Pack
from .store import Engagement, Supplier, UploadRow
from .textnorm import name_similarity
from .understand import Fields

# A scope that names the service only to exclude it ("excluding logistics operations") does not cover it.
_EXCLUSION = re.compile(r"\b(?:except|excluding|excludes|excluded|other than|not including|with the exception of|"
                        r"outside)\b", re.I)
# What a signature line says when nobody signed it: a template's placeholder, not a name.
_PLACEHOLDER = re.compile(r"^(?:\W*(?:n/?a|tbc|tbd|to be signed|unsigned|not signed|pending|name|signature|title|date|"
                          r"print name|authori[sz]ed signatory)\W*|\[.*\]|\(.*\)|[\W_]*)$", re.I)


@dataclass
class CheckContext:
    pack: Pack
    as_of: date
    engagement: Engagement
    supplier: Supplier
    insured_party: Supplier          # the parent, when the document is a group policy covering this supplier
    via_parent: bool
    document_id: int
    fields: Fields
    uploads: list[UploadRow]
    source: str                      # "text" or "ocr"
    hint_values: dict[str, Any] = field(default_factory=dict)


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _hint(spec: CheckSpec, values: dict[str, Any]) -> str:
    return string.Formatter().vformat(spec.hint, (), _Safe(values)) if spec.hint else ""


def _result(cid: str, rid: str, spec: CheckSpec, verdict: Verdict, reason: str, ctx: CheckContext,
            evidence: list[Evidence] | None = None, lapsed: bool = False, **hint) -> CheckResult:
    values = {"supplier": ctx.supplier.legal_name, "tier": ctx.engagement.tier, **hint}
    return CheckResult(check_id=cid, requirement_id=rid, verdict=verdict, severity=spec.severity, tier=spec.tier,
                       reason=reason, evidence=evidence or [], lapsed=lapsed,
                       fix_hint=_hint(spec, values) if verdict == Verdict.FAIL else None)


def _ev(ctx: CheckContext, fv: FieldValue) -> Evidence:
    return Evidence(document_id=ctx.document_id, page=fv.page, quote=fv.line, source=fv.source or ctx.source)


def _value(ctx: CheckContext, name: str) -> tuple[FieldValue | None, str | None]:
    """The field's value, or a reason it cannot be used."""
    if name in ctx.fields.conflicts:
        vals = "; ".join(f"'{v.raw}' (page {v.page})" for v in ctx.fields.conflicts[name])
        return None, f"the document states two different values: {vals}"
    fv = ctx.fields.values.get(name)
    if fv is None:
        if name in ctx.fields.unparsed:
            return None, f"value present but could not be read reliably: '{ctx.fields.unparsed[name][0]}'"
        return None, "value not found on the document"
    return fv, None


def evaluate(cid: str, rid: str, ctx: CheckContext) -> CheckResult:
    spec = ctx.pack.checks[cid]
    rule = RULES[spec.rule]
    return rule(cid, rid, spec, ctx, spec.params)


def _uploaded_before_start(cid, rid, spec, ctx, p):
    first = min(u.uploaded_at for u in ctx.uploads).date()
    start = ctx.engagement.start_date
    ev = [Evidence(document_id=ctx.document_id, quote=f"first received {first.isoformat()}", source="metadata")]
    if first <= start:
        return _result(cid, rid, spec, Verdict.PASS, "received before the engagement started", ctx, ev)
    return _result(cid, rid, spec, Verdict.FAIL, f"received {(first - start).days} days after the start", ctx, ev,
                   days_late=(first - start).days, start_date=start.isoformat())


def _channel_in(cid, rid, spec, ctx, p):
    channels = sorted({u.channel for u in ctx.uploads})
    ev = [Evidence(document_id=ctx.document_id, quote=f"channels: {', '.join(channels)}", source="metadata")]
    if any(c in p["allowed"] for c in channels):
        return _result(cid, rid, spec, Verdict.PASS, "received through a verified channel", ctx, ev)
    return _result(cid, rid, spec, Verdict.FAIL, f"only received via {', '.join(channels)}", ctx, ev,
                   channel=", ".join(channels))


def _name_matches(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    expected = ctx.insured_party.legal_name if p["against"] == "insured_party" else ctx.supplier.legal_name
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    score = name_similarity(fv.value, expected)
    if score >= p["pass_at"]:
        return _result(cid, rid, spec, Verdict.PASS, f"'{fv.value}' matches '{expected}' ({score:.0f})", ctx, [_ev(ctx, fv)])
    if score < p["fail_below"]:
        return _result(cid, rid, spec, Verdict.FAIL, f"'{fv.value}' is not '{expected}' ({score:.0f})", ctx,
                       [_ev(ctx, fv)], found=fv.value, expected=expected)
    return _result(cid, rid, spec, Verdict.ABSTAIN, f"'{fv.value}' is close to '{expected}' ({score:.0f}) but not the "
                   "same; a person should confirm", ctx, [_ev(ctx, fv)])


def _money_at_least(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    amount, currency = fv.value
    required = ctx.pack.tier_minimum_liability[ctx.engagement.tier]
    if currency != "EUR":
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"limit stated in {currency}; no conversion rule", ctx, [_ev(ctx, fv)])
    if amount >= required:
        return _result(cid, rid, spec, Verdict.PASS, f"limit EUR {amount:,.0f} meets EUR {required:,}", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"limit EUR {amount:,.0f} is below EUR {required:,}", ctx, [_ev(ctx, fv)],
                   found=f"EUR {amount:,.0f}", required=f"EUR {required:,}")


def _date_covers(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    target = ctx.as_of + timedelta(days=p.get("margin_days", 0))
    if p.get("margin_days", 0) > 0 and fv.value < ctx.as_of:
        return _result(cid, rid, spec, Verdict.SKIPPED, "already lapsed; the expiry check reports it", ctx, [_ev(ctx, fv)])
    if fv.value >= target:
        return _result(cid, rid, spec, Verdict.PASS, f"{fv.value.isoformat()} is on or after {target.isoformat()}", ctx,
                       [_ev(ctx, fv)])
    lapsed = fv.value < ctx.as_of
    return _result(cid, rid, spec, Verdict.FAIL,
                   f"{fv.value.isoformat()} is before {target.isoformat()}" + (" (already lapsed)" if lapsed else ""),
                   ctx, [_ev(ctx, fv)], lapsed=lapsed, found=fv.value.isoformat(), required=target.isoformat())


def _within_days(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    age = (ctx.as_of - fv.value).days
    if age < -1:
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"dated {fv.value.isoformat()}, in the future", ctx, [_ev(ctx, fv)])
    if age <= p["days"]:
        return _result(cid, rid, spec, Verdict.PASS, f"dated {fv.value.isoformat()}, {age} days ago", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"dated {fv.value.isoformat()}, {age} days ago (limit {p['days']})", ctx,
                   [_ev(ctx, fv)], found=fv.value.isoformat())


def _in_allowlist(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    allowed = getattr(ctx.pack, p["allowlist"])
    best = max((name_similarity(fv.value, a), a) for a in allowed)
    if best[0] >= p["pass_at"]:
        return _result(cid, rid, spec, Verdict.PASS, f"'{fv.value}' is accepted ({best[1]})", ctx, [_ev(ctx, fv)])
    if best[0] >= 80:
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"'{fv.value}' resembles '{best[1]}' ({best[0]:.0f})", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"'{fv.value}' is not on the accepted list", ctx, [_ev(ctx, fv)],
                   found=fv.value)


def _scope_mentions(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    terms = ctx.pack.service_terms[ctx.engagement.service_category]
    hit = next((t for t in terms if t in fv.value.casefold()), None)
    if hit and _EXCLUSION.search(fv.value):
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"the scope mentions '{hit}' but also an exclusion; a person "
                       "reads which applies", ctx, [_ev(ctx, fv)])
    if hit:
        return _result(cid, rid, spec, Verdict.PASS, f"scope mentions '{hit}'", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"scope does not mention {', '.join(terms)}", ctx, [_ev(ctx, fv)],
                   required=" or ".join(terms))


def _iban_checksum(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    if iban_valid(fv.value):
        return _result(cid, rid, spec, Verdict.PASS, "IBAN check digits are valid", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, "IBAN check digits are invalid", ctx, [_ev(ctx, fv)])


def _matches_record(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    on_file = getattr(ctx.supplier, p["record"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    if not on_file:
        return _result(cid, rid, spec, Verdict.ABSTAIN, "nothing on file to compare with", ctx, [_ev(ctx, fv)])
    if compact(fv.value) == compact(on_file):
        return _result(cid, rid, spec, Verdict.PASS, "matches the record on file", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, "differs from the record on file", ctx, [_ev(ctx, fv)])


def _vat_format(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    if vat_valid(fv.value):
        return _result(cid, rid, spec, Verdict.PASS, "VAT number format is valid", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, "VAT number format is invalid", ctx, [_ev(ctx, fv)], found=fv.value)


def _lei_checksum(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        if p.get("optional") and p["field"] not in ctx.fields.conflicts and p["field"] not in ctx.fields.unparsed:
            return _result(cid, rid, spec, Verdict.SKIPPED, "no LEI on the document; it is optional", ctx)
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    if lei_valid(fv.value):
        return _result(cid, rid, spec, Verdict.PASS, "LEI check digits are valid", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, "LEI check digits are invalid", ctx, [_ev(ctx, fv)], found=fv.value)


def _both_signed(cid, rid, spec, ctx, p):
    missing = []
    evidence = []
    for name in p["fields"]:
        fv = ctx.fields.values.get(name)
        if fv is None or not any(ch.isalpha() for ch in str(fv.value)) or _PLACEHOLDER.match(str(fv.value).strip()):
            missing.append(name.replace("_signatory", ""))
        else:
            evidence.append(_ev(ctx, fv))
    if not missing:
        return _result(cid, rid, spec, Verdict.PASS, "signed by both parties", ctx, evidence)
    return _result(cid, rid, spec, Verdict.FAIL, f"no signature found for: {', '.join(missing)}", ctx, evidence)


RULES = {
    "uploaded_before_engagement_start": _uploaded_before_start,
    "channel_in": _channel_in,
    "name_matches": _name_matches,
    "money_at_least": _money_at_least,
    "date_covers": _date_covers,
    "within_days": _within_days,
    "in_allowlist": _in_allowlist,
    "scope_mentions": _scope_mentions,
    "iban_checksum": _iban_checksum,
    "matches_record": _matches_record,
    "vat_format": _vat_format,
    "lei_checksum": _lei_checksum,
    "both_signed": _both_signed,
}
