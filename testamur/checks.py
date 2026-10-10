"""Deterministic check rules, for the metadata and field tiers.

Every rule returns PASS, FAIL or ABSTAIN with the evidence it used. A missing, unparseable or self-contradicting
value is an ABSTAIN, never a PASS: the engine does not round up."""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .ids import compact, gstin_valid, ifsc_valid, lei_valid, pan_of_gstin, pan_valid
from .model import CheckResult, Evidence, FieldValue, Verdict
from .pack import CheckSpec, Pack
from .store import Claim, Creditor, Matter, UploadRow
from .textnorm import format_inr, name_similarity, rupees_in_words
from .understand import Fields

# A scope that names a power or an asset only to exclude it ("excluding voting on any resolution plan") does not
# cover it.
_EXCLUSION = re.compile(r"\b(?:except|excluding|excludes|excluded|other than|not including|with the exception of|"
                        r"outside|save for)\b", re.I)
# What a signature line says when nobody signed it: a template's placeholder, not a name.
_PLACEHOLDER = re.compile(r"^(?:\W*(?:n/?a|tbc|tbd|to be signed|unsigned|not signed|pending|name|signature|title|date|"
                          r"print name|authori[sz]ed signatory)\W*|\[.*\]|\(.*\)|[\W_]*)$", re.I)


@dataclass
class CheckContext:
    pack: Pack
    as_of: date
    claim: Claim
    creditor: Creditor
    filing_party: Creditor           # the trustee, when the document is a trustee's authority covering this creditor
    via_trustee: bool
    matter: Matter                   # the insolvency process: the corporate debtor and its dates
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
    values = {"creditor": ctx.creditor.legal_name, "tier": ctx.claim.tier, **hint}
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


# Rules whose verdict depends on the as-of date: a verdict from yesterday is not today's (an authority can lapse
# overnight).
TIME_RULES = frozenset({"date_covers", "within_days"})


def evaluate(cid: str, rid: str, ctx: CheckContext) -> CheckResult:
    spec = ctx.pack.checks[cid]
    rule = RULES[spec.rule]
    return rule(cid, rid, spec, ctx, spec.params)


def _received_by_due_date(cid, rid, spec, ctx, p):
    first = min(u.uploaded_at for u in ctx.uploads).date()
    due = ctx.matter.claims_due
    ev = [Evidence(document_id=ctx.document_id, quote=f"first received {first.isoformat()}", source="metadata")]
    if first <= due:
        return _result(cid, rid, spec, Verdict.PASS, "received by the last date for claims", ctx, ev)
    return _result(cid, rid, spec, Verdict.FAIL, f"received {(first - due).days} days after the last date for claims",
                   ctx, ev, days_late=(first - due).days, due_date=due.isoformat())


def _channel_in(cid, rid, spec, ctx, p):
    channels = sorted({u.channel for u in ctx.uploads})
    ev = [Evidence(document_id=ctx.document_id, quote=f"channels: {', '.join(channels)}", source="metadata")]
    if any(c in p["allowed"] for c in channels):
        return _result(cid, rid, spec, Verdict.PASS, "received through a verified channel", ctx, ev)
    return _result(cid, rid, spec, Verdict.FAIL, f"only received via {', '.join(channels)}", ctx, ev,
                   channel=", ".join(channels))


def _expected_name(ctx: CheckContext, against: str) -> str:
    if against == "filing_party":
        return ctx.filing_party.legal_name
    if against == "debtor":
        return ctx.matter.name
    return ctx.creditor.legal_name


def _name_matches(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    expected = _expected_name(ctx, p["against"])
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


def _rupees(ctx: CheckContext, fv: FieldValue) -> Decimal | None:
    amount, currency = fv.value
    return amount if currency == "INR" else None


def _money_at_least(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    amount, required = _rupees(ctx, fv), getattr(ctx.claim, p["record"])
    if amount is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"amount stated in {fv.value[1]}; no conversion rule", ctx,
                       [_ev(ctx, fv)])
    if amount >= required:
        return _result(cid, rid, spec, Verdict.PASS, f"{format_inr(amount)} meets {format_inr(required)}", ctx,
                       [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"{format_inr(amount)} is below {format_inr(required)}", ctx,
                   [_ev(ctx, fv)], found=format_inr(amount), required=format_inr(required))


def _money_within_record(cid, rid, spec, ctx, p):
    """A claim for no more than the corporate debtor's books show, within a tolerance for interest accrued since the
    books were closed. Claiming less is the creditor's right; claiming more needs the documents behind it."""
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    amount, books = _rupees(ctx, fv), getattr(ctx.claim, p["record"])
    if amount is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"amount stated in {fv.value[1]}; no conversion rule", ctx,
                       [_ev(ctx, fv)])
    if not books:
        return _result(cid, rid, spec, Verdict.ABSTAIN, "the corporate debtor's books show no balance to compare with",
                       ctx, [_ev(ctx, fv)])
    if amount <= Decimal(books) * Decimal(str(1 + p["tolerance"])):
        return _result(cid, rid, spec, Verdict.PASS, f"{format_inr(amount)} is within the books ({format_inr(books)})",
                       ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"{format_inr(amount)} exceeds the books ({format_inr(books)})", ctx,
                   [_ev(ctx, fv)], found=format_inr(amount), required=format_inr(books))


def _words_match_figures(cid, rid, spec, ctx, p):
    figures, why_f = _value(ctx, p["figures"])
    words, why_w = _value(ctx, p["words"])
    if figures is None or words is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why_f or why_w, ctx)
    amount = _rupees(ctx, figures)
    evidence = [_ev(ctx, figures), _ev(ctx, words)]
    if amount is not None and amount == words.value:
        return _result(cid, rid, spec, Verdict.PASS, "the amount in words agrees with the figures", ctx, evidence)
    return _result(cid, rid, spec, Verdict.FAIL, f"words say {format_inr(words.value)}, figures say "
                   f"{format_inr(amount) if amount is not None else figures.raw}", ctx, evidence,
                   found=rupees_in_words(words.value), required=figures.raw)


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                      # 29 February in a year that has none
        return d.replace(year=d.year + years, day=28)


def _date_not_before(cid, rid, spec, ctx, p):
    """A date on the document no earlier than a date of the process, shifted by whole years: a default within the
    three years before the insolvency commencement date, a form signed after the public announcement. The anchor is
    fixed for the process, so the verdict does not change with the calendar."""
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    anchor = getattr(ctx.matter, p["anchor"])
    limit = _shift_years(anchor, p.get("offset_years", 0))
    if fv.value >= limit:
        return _result(cid, rid, spec, Verdict.PASS, f"{fv.value.isoformat()} is on or after {limit.isoformat()}", ctx,
                       [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"{fv.value.isoformat()} is before {limit.isoformat()}", ctx,
                   [_ev(ctx, fv)], found=fv.value.isoformat(), anchor=anchor.isoformat())


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
    terms = p.get("terms") or ctx.pack.asset_terms[getattr(ctx.claim, p["terms_from"])]
    hit = next((t for t in terms if t in fv.value.casefold()), None)
    if hit and _EXCLUSION.search(fv.value):
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"the text mentions '{hit}' but also an exclusion; a person "
                       "reads which applies", ctx, [_ev(ctx, fv)])
    if hit:
        return _result(cid, rid, spec, Verdict.PASS, f"mentions '{hit}'", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, f"does not mention {', '.join(terms)}", ctx, [_ev(ctx, fv)],
                   required=" or ".join(terms))


def _gstin_checksum(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    if gstin_valid(fv.value):
        return _result(cid, rid, spec, Verdict.PASS, "the GSTIN's check character is valid", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, "the GSTIN is malformed or its check character is wrong", ctx,
                   [_ev(ctx, fv)], found=fv.value)


def _pan_in_gstin(cid, rid, spec, ctx, p):
    """The PAN is characters 3 to 12 of the GSTIN. A form whose PAN and GSTIN disagree names two entities."""
    pan, why_p = _value(ctx, p["pan"])
    gstin, why_g = _value(ctx, p["gstin"])
    if pan is None or gstin is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why_p or why_g, ctx)
    evidence = [_ev(ctx, pan), _ev(ctx, gstin)]
    if not pan_valid(pan.value) or len(compact(gstin.value)) != 15:
        return _result(cid, rid, spec, Verdict.ABSTAIN, "the PAN or the GSTIN is malformed; a person reads them", ctx,
                       evidence)
    inside = pan_of_gstin(gstin.value)
    if compact(pan.value) == inside:
        return _result(cid, rid, spec, Verdict.PASS, "the PAN is the one inside the GSTIN", ctx, evidence)
    return _result(cid, rid, spec, Verdict.FAIL, f"PAN {compact(pan.value)} is not {inside}, the PAN in the GSTIN", ctx,
                   evidence, found=compact(pan.value), required=inside)


def _ifsc_valid(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    code = compact(fv.value)
    if not ifsc_valid(code):
        return _result(cid, rid, spec, Verdict.FAIL, f"'{fv.value}' is not a well-formed IFSC", ctx, [_ev(ctx, fv)],
                       found=fv.value)
    bank, _ = _value(ctx, p["bank_field"])
    if bank is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, "no bank named to compare the IFSC with", ctx, [_ev(ctx, fv)])
    scored = max((name_similarity(bank.value, name), name) for name in ctx.pack.bank_codes)
    if scored[0] < 92:
        return _result(cid, rid, spec, Verdict.ABSTAIN, f"'{bank.value}' is not a bank whose IFSC code is known", ctx,
                       [_ev(ctx, fv), _ev(ctx, bank)])
    expected = ctx.pack.bank_codes[scored[1]]
    if code[:4] == expected:
        return _result(cid, rid, spec, Verdict.PASS, f"IFSC of {scored[1]} ({expected})", ctx, [_ev(ctx, fv), _ev(ctx, bank)])
    return _result(cid, rid, spec, Verdict.FAIL, f"IFSC code {code[:4]} is not {scored[1]}'s ({expected})", ctx,
                   [_ev(ctx, fv), _ev(ctx, bank)], found=fv.value)


def _matches_record(cid, rid, spec, ctx, p):
    fv, why = _value(ctx, p["field"])
    on_file = getattr(ctx.creditor, p["record"])
    if fv is None:
        return _result(cid, rid, spec, Verdict.ABSTAIN, why, ctx)
    if not on_file:
        return _result(cid, rid, spec, Verdict.ABSTAIN, "nothing on file to compare with", ctx, [_ev(ctx, fv)])
    if compact(fv.value) == compact(on_file):
        return _result(cid, rid, spec, Verdict.PASS, "matches the record on file", ctx, [_ev(ctx, fv)])
    return _result(cid, rid, spec, Verdict.FAIL, "differs from the record on file", ctx, [_ev(ctx, fv)])


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
    "received_by_due_date": _received_by_due_date,
    "channel_in": _channel_in,
    "name_matches": _name_matches,
    "money_at_least": _money_at_least,
    "money_within_record": _money_within_record,
    "words_match_figures": _words_match_figures,
    "date_not_before": _date_not_before,
    "date_covers": _date_covers,
    "within_days": _within_days,
    "in_allowlist": _in_allowlist,
    "scope_mentions": _scope_mentions,
    "gstin_checksum": _gstin_checksum,
    "pan_in_gstin": _pan_in_gstin,
    "ifsc_valid": _ifsc_valid,
    "matches_record": _matches_record,
    "lei_checksum": _lei_checksum,
    "both_signed": _both_signed,
}
