# 0012: The first workflow automated is chasing a creditor's lapsing authority

## Context
Many findings could become automated work. Automating the wrong one first spends effort where the engine is least
sure, the volume is low, or a mistake is expensive to undo.

## Decision
Chase the creditor when its representative's authority is missing, expired, not satisfied or due for renewal: a task
with a due date set by the claim's tier, and a reminder to the creditor at most once a week. While the authority (or
the bank details) is expired or not satisfied, the claim is on hold: its votes are not counted and nothing is paid
to it, and the hold is released by itself when the evidence is put right. Every other unmet requirement becomes a
follow-up task for the resolution professional's team.

Why this one first:
- **Volume.** Every financial creditor votes through a representative, and authorities are issued for a period: a
  process that runs for months meets lapsed and lapsing authorities as a matter of course.
- **Decided by rules.** Validity, renewal and the power to vote are dates and words on the page, judged by the field
  tier. No model judgement stands between the finding and the action.
- **Reversible.** A reminder and a task cost little if they turn out to be unnecessary; a hold is released by the
  same machinery that placed it.
- **It matters before every vote.** A meeting of the committee of creditors with a representative whose authority
  lapsed last week is a vote someone can challenge. The outcome is a renewed authority, and time to it is the metric
  (the simulation reports the median days from chase to close).

Not first:
- *Changed bank details.* Higher stakes (the payment-fraud pattern), but the right action is a call-back to a known
  number by a person; automation should only stop payment, and it does, through the hold.
- *Agreement clauses.* Judged by the model tier, and the remedy is evidence from the creditor, not a reminder.
- *Claims outside limitation or above the books.* The remedy is a decision by the resolution professional, with
  reasons, not a chase.

## Consequences
- The policy is configuration (the pack's `actions` section): other requirements can be moved to creditor chasing
  once their checks have earned it, which the audits of ADR 0010 measure.
