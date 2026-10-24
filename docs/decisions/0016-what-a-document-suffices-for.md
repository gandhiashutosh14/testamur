# 0016: Whether a document suffices, and what the evidence permits, are separate decisions

## Context
An external review found two problems in one place. "The representative is not authorised to vote on any
resolution." passed the voting check, because the rule looked for the word "vote". And even a correct FAIL there
could not stop a vote: the check is major, so the authority read SATISFIED_WITH_FINDINGS, and holds were placed only
for EXPIRED or NOT_SATISFIED. A finding that is minor for the document (it is good enough to file the claim on) was
treated as minor for the vote, where it is decisive.

The review also pointed at the limitation check: three years from default is the usual case, but a written
acknowledgement can extend it, and a FAIL on the date alone states a legal conclusion the rule cannot reach.

## Decision
- **The vote is granted by a clause that grants it.** `grants_power` reads the powers clause by clause: a clause
  that names the power and withholds it (not, no, may not, withdrawn, revoked, excluding, save for) is a FAIL; one
  that makes it depend on a condition, an approval or a later date, narrows it ("only on"), or sits beside a clause
  withholding part of it, is a question for a person; no clause naming the power is a FAIL. Narrow positive reading,
  not a growing list of negative keywords. An authority dated to take effect later is not in effect yet
  (`auth_in_effect`, judged again when the date moves).
- **Permissions are decided apart from statuses** (`permissions` in the pack). To vote needs a proof of claim, an
  authority in a vouching status and `auth_covers_voting` PASS; a payout needs a proof of claim and bank details with
  no findings at all. Every condition must hold; each unmet one is named. The engine stores a permission per claim
  and action and reports changes through the outbox; the dispatcher keeps one hold per claim and action mirroring it.
- **The engine recommends; a person acts.** Votes and payments happen elsewhere and are simulated here. A
  conditional grant becomes a review item; a person's decision on the check is an input like any other.
- **Rules that state only the usual case of the law say so.** A check can be marked `illustrative`, with the date
  its wording was reviewed and its basis; its exceptions are left to a person. `claim_not_time_barred` now abstains
  outside three years instead of failing, with the question a person must answer (an acknowledgement on file?).
- **Measured**: the harness compares each claim's permissions with the truth's, and a vote or payout permitted when
  the truth withholds it is a merge gate at zero.

## Consequences
- More claims are held from voting: any claim without a satisfied proof of claim, or with an authority that does not
  plainly grant the vote, where before only expired or failed authorities held it. That is the conservative reading.
- Time-barred claims become review items, not rejections: more deferrals, and no claim rejected on a rule that cannot
  see an acknowledgement.

## Alternatives considered
- *Make `auth_covers_voting` critical.* The authority would read NOT_SATISFIED for a representative entitled to file,
  hiding the difference that matters: the document is fine for filing, the vote is not.
- *Keep extending the exclusion pattern.* Each new negation would be found by the next review.
