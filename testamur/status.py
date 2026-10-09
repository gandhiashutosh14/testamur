"""Turn a requirement's checks into one status. The order of the rules encodes the policy: uncertainty is never
rounded up, a lapse is reported as a lapse, and only clean evidence earns a vouching status."""
from __future__ import annotations

from .model import VOUCHING, CheckResult, Severity, Status, Verdict


def decide(*, applicable: bool, results: list[CheckResult] | None, via_parent: bool = False,
           attested: bool = False, pending_review: bool = False, hostile: bool = False) -> tuple[Status, list[str]]:
    """`results` is None when no current document matched the requirement.

    `pending_review` means an unresolved document (an abstained match) could belong to this supplier. Then a
    negative status (missing, expired, not satisfied) is not reported: the pending document may be the renewal,
    so a person resolves it first.

    `hostile` means the deciding document carries text aimed at automated readers. Its rule checks may all pass
    (rules do not follow instructions), but a document that tries to manipulate its reviewer is itself a red flag:
    it never earns a vouching status until a person has cleared the security finding."""
    status, reasons = _decide(applicable, results, via_parent, attested)
    if hostile and results is not None and status in VOUCHING:
        return Status.NEEDS_REVIEW, reasons + [f"the document carries text aimed at automated readers; a person must "
                                               f"clear it (would be {status.value})"]
    if pending_review and status in (Status.MISSING, Status.EXPIRED, Status.NOT_SATISFIED):
        return Status.NEEDS_REVIEW, reasons + [f"an unresolved document may change this (would be {status.value})"]
    return status, reasons


def _decide(applicable: bool, results: list[CheckResult] | None, via_parent: bool,
            attested: bool) -> tuple[Status, list[str]]:
    if not applicable:
        return Status.NOT_REQUIRED, ["does not apply to this engagement"]
    if results is None:
        if attested:
            return Status.VERIFIED_OUT_OF_BAND, ["verified out of band; no document"]
        return Status.MISSING, ["no matching document"]
    critical = [r for r in results if r.severity == Severity.CRITICAL]
    reasons = [f"{r.check_id}: {r.verdict}" + (f" ({r.reason})" if r.verdict != Verdict.PASS else "")
               for r in results if r.verdict != Verdict.SKIPPED]
    if not any(r.verdict in (Verdict.PASS, Verdict.FAIL) for r in results):
        return Status.NEEDS_REVIEW, reasons + ["no check could be evaluated; a requirement nobody checked is not met"]
    if any(r.verdict == Verdict.ABSTAIN for r in critical):
        return Status.NEEDS_REVIEW, reasons
    critical_fails = [r for r in critical if r.verdict == Verdict.FAIL]
    if critical_fails:
        if all(r.lapsed for r in critical_fails):
            return Status.EXPIRED, reasons
        return Status.NOT_SATISFIED, reasons
    findings = [r for r in results if r.verdict in (Verdict.FAIL, Verdict.ABSTAIN)]
    if via_parent:
        return Status.COVERED_BY_PARENT, reasons
    if findings:
        return Status.SATISFIED_WITH_FINDINGS, reasons
    return Status.SATISFIED, reasons
