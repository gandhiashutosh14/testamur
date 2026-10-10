# 0012: The first workflow automated is chasing creditors for insurance

## Context
Many findings could become automated work. Automating the wrong one first spends effort where the engine is least
sure, the volume is low, or a mistake is expensive to undo.

## Decision
Chase the creditor when insurance is missing, expired, not satisfied or due for renewal: a task with a due date set
by the claim's risk tier, and a reminder to the creditor at most once a week. While insurance (or bank details)
is expired or not satisfied, the claim is on a purchasing hold, released by itself when the evidence is put
right. Every other unmet requirement becomes a follow-up task for the buyer's staff.

Why this one first:
- **Volume.** Every creditor has insurance and every policy renews every year: the most frequent finding there is.
- **Decided by rules.** Expiry, renewal and limit are dates and amounts, judged by the field tier; in the harness
  those checks agree with the truth on every document. No model judgement stands between the finding and the action.
- **Reversible.** A reminder and a task cost little if they turn out to be unnecessary; a hold is released by the
  same machinery that placed it.
- **Measurable.** The outcome is a renewed certificate, and time to it is the metric (the simulation reports the
  median days from chase to close).

Not first:
- *Changed bank details.* Higher stakes (the invoice-fraud pattern), but the right action is a call-back to a known
  number by a person; automation should only stop payment, and it does, through the hold.
- *Contract clauses.* Judged by the model tier, lower volume, and the remedy is a negotiation, not a reminder.
- *Security certifications.* Valid for three years: far fewer events.

## Consequences
- The policy is configuration (the pack's `actions` section): other requirements can be moved to creditor chasing
  once their checks have earned it, which the audits of ADR 0010 measure.
