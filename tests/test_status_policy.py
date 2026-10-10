from testamur.model import CheckResult, Severity, Status, Tier, Verdict
from testamur.status import decide


def r(check, verdict, severity=Severity.CRITICAL, lapsed=False):
    return CheckResult(check_id=check, requirement_id="req", verdict=verdict, severity=severity, tier=Tier.FIELD,
                       reason="test", lapsed=lapsed)


def test_applicability_and_absence():
    assert decide(applicable=False, results=None)[0] == Status.NOT_REQUIRED
    assert decide(applicable=True, results=None)[0] == Status.MISSING
    assert decide(applicable=True, results=None, attested=True)[0] == Status.VERIFIED_OUT_OF_BAND


def test_uncertainty_on_a_critical_check_is_never_rounded_up():
    status, _ = decide(applicable=True, results=[r("a", Verdict.PASS), r("b", Verdict.ABSTAIN)])
    assert status == Status.NEEDS_REVIEW


def test_a_requirement_nobody_could_check_is_not_satisfied():
    status, reasons = decide(applicable=True, results=[r("a", Verdict.SKIPPED), r("b", Verdict.SKIPPED, Severity.MINOR)])
    assert status == Status.NEEDS_REVIEW
    assert any("nobody checked" in x for x in reasons)


def test_lapse_is_reported_as_expired_but_another_critical_failure_wins():
    assert decide(applicable=True, results=[r("exp", Verdict.FAIL, lapsed=True)])[0] == Status.EXPIRED
    both = [r("exp", Verdict.FAIL, lapsed=True), r("name", Verdict.FAIL)]
    assert decide(applicable=True, results=both)[0] == Status.NOT_SATISFIED


def test_findings_and_trustee_cover():
    minor = [r("a", Verdict.PASS), r("b", Verdict.FAIL, Severity.MINOR)]
    assert decide(applicable=True, results=minor)[0] == Status.SATISFIED_WITH_FINDINGS
    assert decide(applicable=True, results=[r("a", Verdict.PASS)], via_trustee=True)[0] == Status.COVERED_BY_TRUSTEE
    assert decide(applicable=True, results=[r("a", Verdict.PASS)])[0] == Status.SATISFIED


def test_a_pending_document_turns_bad_news_into_review_but_never_good_news():
    expired = [r("exp", Verdict.FAIL, lapsed=True)]
    assert decide(applicable=True, results=expired, pending_review=True)[0] == Status.NEEDS_REVIEW
    assert decide(applicable=True, results=None, pending_review=True)[0] == Status.NEEDS_REVIEW
    assert decide(applicable=True, results=[r("a", Verdict.PASS)], pending_review=True)[0] == Status.SATISFIED


def test_a_pending_document_that_may_be_the_creditors_own_suspends_trustee_cover():
    """Found by the generated world: a creditor's own lapsed authority, misfiled and waiting for a person, while the
    trustee's authority listed them. The creditor's own document takes precedence, so the cover cannot vouch."""
    clean = [r("a", Verdict.PASS)]
    assert decide(applicable=True, results=clean, via_trustee=True)[0] == Status.COVERED_BY_TRUSTEE
    status, reasons = decide(applicable=True, results=clean, via_trustee=True, pending_review=True)
    assert status == Status.NEEDS_REVIEW and any("take precedence" in x for x in reasons)


def test_hostile_evidence_never_vouches_even_when_every_rule_passes():
    clean = [r("a", Verdict.PASS)]
    assert decide(applicable=True, results=clean, hostile=True)[0] == Status.NEEDS_REVIEW
    assert decide(applicable=True, results=[r("x", Verdict.FAIL)], hostile=True)[0] == Status.NOT_SATISFIED
