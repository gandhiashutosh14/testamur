# Review simulation

Commit `f16af51`, command `testamur reviewsim --world data/world_full11 --db data/reviewsim.db --report reports/review-sim-full-seed11.md --cassette reports/cassettes/full-seed11.jsonl`. World seed 11 (full). The reviewer and the auditors know the ground truth: this measures the machinery around people, not people.

## 1. Working the queue

460 claim-requirement pairs. Before review, against the truth as a careful person would settle it: 414 exact, 45 waiting for a person, 0 false vouching.

| Decision | Count |
|---|---|
| check | 73 |
| clear | 17 |
| reject | 34 |

After review: 460 of 460 exact, 0 still waiting, 0 false vouching, 0 false alarm, 0 other. A full re-index afterwards changed 0 statuses (decisions are inputs, so it should change none).

## 2. Holding accuracy against a planted fault

Fault `limitation-years`: the rule behind `claim_not_time_barred` reads the limitation period as five years instead of three. Audit rate by severity: {'critical': 0.1, 'major': 0.05, 'minor': 0.02}; seed 1.

Before any audit, against the truth: 436 exact, **5 false vouching** caused by the fault.

| Tenant | False PASS verdicts | Audits drawn | ... of the faulty check | Overturned | Breaker tripped after |
|---|---|---|---|---|---|
| 1 | 2 | 57 | 6 | 1 | 47 |
| 2 | 3 | 48 | 2 | 0 | not tripped |
| 3 | 0 | 41 | 2 | 0 | not tripped |

After the audits: **3 false vouching** left (42 statuses now wait for a person). Working the queue then took: check 68, clear 17, confirm 25, reject 34. Finally 457 of 460 statuses exact against the settled truth, 3 false vouching.

How likely a sample at each rate is to draw at least one of the fault's false PASS verdicts (1 - (1 - rate)^n, per tenant):

| Audit rate | tenant 1 | tenant 2 | tenant 3 |
|---|---|---|---|
| 2% | 4% | 6% | 0% |
| 5% | 10% | 14% | 0% |
| 10% | 19% | 27% | 0% |
| 25% | 44% | 58% | 0% |
| 50% | 75% | 88% | 0% |

A breaker that has not tripped is not a pass: the fault keeps producing false PASS verdicts until a sample draws one, so the audit rate is a choice between review effort and time to detection.
