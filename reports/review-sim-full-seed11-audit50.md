# Review simulation

Commit `6b2b49d`, command `testamur reviewsim --world data/world_full11 --db data/reviewsim50.db --scenarios fault --audit-rate 0.5 --report reports/review-sim-full-seed11-audit50.md --cassette reports/cassettes/full-seed11.jsonl`. World seed 11 (full). The reviewer and the auditors know the ground truth: this measures the machinery around people, not people.

## 2. Holding accuracy against a planted fault

Fault `limitation-years`: the rule behind `claim_not_time_barred` reads the limitation period as five years instead of three. Audit rate by severity: {'critical': 0.5, 'major': 0.5, 'minor': 0.5}; seed 1.

Before any audit, against the truth: 410 exact, **5 false vouching** caused by the fault.

| Tenant | False PASS verdicts | Audits drawn | ... of the faulty check | Overturned | Breaker tripped after |
|---|---|---|---|---|---|
| 1 | 2 | 316 | 12 | 0 | not tripped |
| 2 | 3 | 335 | 16 | 0 | not tripped |
| 3 | 0 | 308 | 14 | 0 | not tripped |

After the audits: **5 false vouching** left (14 statuses now wait for a person). Working the queue then took: assign 7, check 75, clear 18, reject 24. Finally 425 of 430 statuses exact against the settled truth, 5 false vouching.

How likely a sample at each rate is to draw at least one of the fault's false PASS verdicts (1 - (1 - rate)^n, per tenant):

| Audit rate | tenant 1 | tenant 2 | tenant 3 |
|---|---|---|---|
| 2% | 4% | 6% | 0% |
| 5% | 10% | 14% | 0% |
| 10% | 19% | 27% | 0% |
| 25% | 44% | 58% | 0% |
| 50% | 75% | 88% | 0% |

A breaker that has not tripped is not a pass: the fault keeps producing false PASS verdicts until a sample draws one, so the audit rate is a choice between review effort and time to detection.
