# 0010: Measure accuracy from random audits; a check that breaks its budget stops vouching

## Context
In production there is no ground truth. The engine's abstentions reach people anyway, but the dangerous errors are
the confident ones: a PASS that should have been a FAIL never asks for help. Measuring how often that happens needs
people to look at verdicts the engine did not ask about, and the measurement is only worth something if it is
unbiased and if it changes what the engine does.

## Decision
- **Random audits.** A share of the engine's conclusive verdicts (PASS and FAIL) is drawn for people to check: 10%
  for critical checks, 5% for major, 2% for minor (the pack's `audit` section). The draw is a hash of a seed and
  the verdict's identity, independent of what the verdict says, so the estimate is unbiased, and reproducible.
- **One question per check:** when the engine says PASS, how often would a person overturn it? Exact one-sided
  Clopper-Pearson bounds at 95%. Only audits and confirmations count; reviews of abstentions are excluded (the hard
  cases by construction would bias the rate).
- **Budgets per severity** (the share of PASS verdicts a person may overturn): critical 1%, major 5%, minor 10%.
  A check is *certified* when the upper bound is within budget (299 clean audits for 1%, 59 for 5%, 29 for 10%), *not
  yet certified* when there is too little evidence, and *breached* when the lower bound exceeds the budget.
- **The breaker.** A breached check's PASS no longer vouches: it is shown as "a person confirms", which holds a
  critical requirement in NEEDS_REVIEW, and every such PASS joins the queue. For a critical check, one confirmed false
  PASS is enough, however it was found (an audit, or a complaint that made someone look): biased evidence for a rate,
  but an incident all the same.
- **Recovery is deliberate.** The breaker resets when a person re-certifies the check after finding the cause, or
  when the rule itself changes (only reviews under the current rule digest count). Either way, measurement starts
  afresh.
- **Per tenant.** A tenant's reviews decide that tenant's breakers only.

## Consequences
- The review simulation plants a fault (a limit compared with a fifth of the tier's minimum) and shows the loop:
  the fault's false vouching, the audit that trips the breaker, no false vouching after it, and the confirmations
  it then costs (`testamur reviewsim`, `reports/review-sim-*.md`).
- Detection is probabilistic: a sample at rate r misses a fault with n false PASS verdicts with probability
  (1 - r)^n. The audit rate is a choice between review effort and time to detection, and the report shows it.
- Certification takes many audits for a 1% budget, so most checks stay "not yet certified" in a small deployment.
  That is a truthful state, not a failure: it does not demote anything.

## Alternatives considered
- *Estimate from the review queue.* Those are the cases the engine already doubted; the estimate would be biased.
- *Score thresholds tuned once on a labelled set.* No signal when the inputs drift, and nothing stops a check that
  has started to fail.
- *Demote only on statistical evidence.* For a critical check, waiting for a significant rate after a confirmed
  false PASS means vouching wrongly in the meantime.
