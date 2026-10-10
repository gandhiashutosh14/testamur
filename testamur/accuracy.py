"""How accurate is each check, and may its PASS still vouch?

Accuracy is estimated only from people's verdicts on verdicts the engine did *not* ask about: random audit samples of
its conclusive verdicts, and confirmations of every PASS of a check under review. Verdicts people gave on the
engine's abstentions are excluded: they are the hard cases by construction and would bias the estimate.

For each check the question that matters is: when the engine says PASS, how often would a person overturn it? The
estimate comes with exact (Clopper-Pearson) one-sided bounds, and the pack sets a budget per severity:

- **certified**: the upper bound is within the budget;
- **not yet certified**: too few audits to tell, which is no reason to stop trusting the check;
- **breached**: the lower bound is above the budget (the check is worse than allowed, with confidence), or, for a
  critical check, any confirmed false PASS however it was found (a random audit, or a complaint that led someone to
  look: biased evidence for a rate, but an incident all the same). A breached check's PASS no longer vouches: a
  person confirms each one until someone has found the cause and re-certifies the check, or the rule is changed.

Only reviews made under the check's current rule digest and after its last re-certification count, so a fix starts
the measurement afresh. Everything is per tenant: one tenant's reviews never decide another tenant's statuses.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .model import Severity, Verdict
from .pack import Pack
from .store import CheckEvent, CheckReview

COUNTED = ("audit", "confirm")       # reviews of verdicts the engine did not ask about


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed in log space."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    total = sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
                for i in range(k + 1))
    return min(1.0, total)


def _solve(f, target: float) -> float:
    """The p in (0, 1) where the decreasing function f(p) crosses target, by bisection."""
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided Clopper-Pearson upper bound: the largest rate still consistent with seeing only k of n."""
    if n == 0 or k >= n:
        return 1.0
    return _solve(lambda p: binom_cdf(k, n, p), alpha)


def cp_lower(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided Clopper-Pearson lower bound: the smallest rate still consistent with seeing as many as k of n."""
    if k <= 0:
        return 0.0
    return _solve(lambda p: binom_cdf(k - 1, n, p), 1 - alpha)


@dataclass
class CheckHealth:
    check_id: str
    severity: str
    budget: float
    audited_pass: int          # engine PASS verdicts a person looked at, in the current window
    false_pass: int            # ... that the person overturned
    audited_fail: int
    false_fail: int
    upper: float | None        # bound on P(a person overturns | the engine said PASS)
    lower: float | None
    incidents: int             # false PASS verdicts confirmed by any review, random or not
    state: str                 # certified | uncertified | breached


def check_health(session: Session, pack: Pack) -> dict[str, CheckHealth]:
    """Health of every check for the session's tenant."""
    since = dict(session.execute(select(CheckEvent.check_id, func.max(CheckEvent.after_review_id))
                                 .group_by(CheckEvent.check_id)).all())
    digests = {cid: pack.rule_digest(cid) for cid in pack.checks}
    latest: dict[tuple, CheckReview] = {}            # one review per verdict: the latest
    for r in session.scalars(select(CheckReview).order_by(CheckReview.id)):
        if r.check_id in digests and r.rule_digest == digests[r.check_id] and r.id > since.get(r.check_id, 0):
            latest[(r.claim_id, r.requirement_id, r.check_id, r.document_id)] = r
    counts: dict[str, Counter] = {}
    for (_, _, cid, _), r in latest.items():
        c = counts.setdefault(cid, Counter())
        c["incidents"] += r.machine_verdict == Verdict.PASS and r.verdict == Verdict.FAIL
        if r.kind not in COUNTED:
            continue
        if r.machine_verdict == Verdict.PASS:
            c["pass"] += 1
            c["false_pass"] += r.verdict == Verdict.FAIL
        elif r.machine_verdict == Verdict.FAIL:
            c["fail"] += 1
            c["false_fail"] += r.verdict == Verdict.PASS
    alpha = 1 - pack.audit.confidence
    out = {}
    for cid, spec in pack.checks.items():
        c = counts.get(cid, Counter())
        n, k = c["pass"], c["false_pass"]
        budget = pack.audit.false_pass_budget[spec.severity.value]
        upper = cp_upper(k, n, alpha) if n else None
        lower = cp_lower(k, n, alpha) if n else None
        if (spec.severity == Severity.CRITICAL and c["incidents"]) or (lower is not None and lower > budget):
            state = "breached"
        elif upper is not None and upper <= budget:
            state = "certified"
        else:
            state = "uncertified"
        out[cid] = CheckHealth(cid, spec.severity.value, budget, n, k, c["fail"], c["false_fail"], upper, lower,
                               c["incidents"], state)
    return out


def audits_needed(budget: float, alpha: float = 0.05) -> int:
    """Clean audits (no false PASS) needed before a check can be certified within its budget."""
    n = 1
    while cp_upper(0, n, alpha) > budget:
        n += 1
    return n
