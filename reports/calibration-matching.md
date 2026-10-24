# Calibrating the name-matching threshold

Commit `f16af51`, command `testamur calibrate --seeds 21-30 --size full --validate-world data/world_full11 --validate-db data/harness_full11.db --report reports/calibration-matching.md`.

Calibration: 1656 documents matched by name in born-digital worlds (seeds 21-30), labelled by the generator's ground truth. Method: Learn then Test, fixed-sequence over thresholds from 100 down in steps of 0.5, exact binomial p-values. The rate certified is the share of accepted documents attached to the wrong creditor (selective risk, tested on the number accepted); the share of all labelled documents that were accepted and wrong is shown beside it. The margin rule (5.0 points over the runner-up) and the exact-name rule are unchanged. Today's hand-set threshold is 92.0.

| alpha (allowed rate of wrong attachments among accepted) | delta | Certified threshold | Accepted at it | Wrong at it | Wrong among accepted | Accepted and wrong, among all |
|---|---|---|---|---|---|---|
| 1% | 5% | 86.5 | 1465 | 8 | 0.55% | 0.48% |
| 2% | 5% | 79.0 | 1477 | 20 | 1.35% | 1.21% |
| 5% | 5% | 70.0 | 1509 | 52 | 3.45% | 3.14% |

Validation on a different world with scanned and photographed documents (world_full11, 154 documents matched by name):

| Threshold | Accepted | Share accepted | Wrong attachments | Wrong among accepted |
|---|---|---|---|---|
| 92.0 (today) | 138 | 89.6% | 0 | 0.00% |
| 86.5 | 139 | 90.3% | 1 | 0.72% |
| 79.0 | 141 | 91.6% | 3 | 2.13% |
| 70.0 | 144 | 93.5% | 6 | 4.17% |

## What this does and does not show

- The guarantee holds for documents like the calibration ones. Scans and photographs are a different distribution (OCR changes names), which is why the threshold is checked on a scanned world as well.
- The generator's near-twin names are one model of how names collide; real creditor registers will collide differently. In production the labels come from people's decisions on matches.
- Accepting more by name only automates more if nothing else holds the document back: claims, misfiled uploads and identifiers still apply.
