# Review simulation

Commit `b52caf3`, command `testamur reviewsim --world data/world_full11 --db data/reviewsim.db --report reports/review-sim-full-seed11.md --cassette reports/cassettes/full-seed11.jsonl`. World seed 11 (full). The reviewer and the auditors know the ground truth: this measures the machinery around people, not people.

## 1. Working the queue

585 engagement-requirement pairs. Before review, against the truth as a careful person would settle it: 547 exact, 34 waiting for a person, 0 false assurance.

| Decision | Count |
|---|---|
| assign | 5 |
| check | 64 |
| clear | 14 |
| reject | 19 |

After review: 585 of 585 exact, 0 still waiting, 0 false assurance, 0 false alarm, 0 other. A full re-index afterwards changed 0 statuses (decisions are inputs, so it should change none).

## 2. Holding accuracy against a planted fault

Fault `limit-scale`: the rule behind `ins_limit_meets_tier` compares a limit with a fifth of the tier's minimum. Audit rate by severity: {'critical': 0.1, 'major': 0.05, 'minor': 0.02}; seed 1.

Before any audit, against the truth: 567 exact, **4 false assurance** caused by the fault.

| Tenant | False PASS verdicts | Audits drawn | ... of the faulty check | Overturned | Breaker tripped after |
|---|---|---|---|---|---|
| 1 | 0 | 33 | 3 | 0 | not tripped |
| 2 | 3 | 36 | 1 | 0 | not tripped |
| 3 | 1 | 35 | 2 | 0 | not tripped |

After the audits: **4 false assurance** left (10 statuses now wait for a person). Working the queue then took: assign 5, check 64, clear 14, reject 19. Finally 581 of 585 statuses exact against the settled truth, 4 false assurance.

How likely a sample at each rate is to draw at least one of the fault's false PASS verdicts (1 - (1 - rate)^n, per tenant):

| Audit rate | tenant 1 | tenant 2 | tenant 3 |
|---|---|---|---|
| 2% | 0% | 6% | 2% |
| 5% | 0% | 14% | 5% |
| 10% | 0% | 27% | 10% |
| 25% | 0% | 58% | 25% |
| 50% | 0% | 88% | 50% |

A breaker that has not tripped is not a pass: the fault keeps producing false PASS verdicts until a sample draws one, so the audit rate is a choice between review effort and time to detection.
