# Calibrating the name-matching threshold

Commit `4298b0e+dirty`, command `testamur calibrate --seeds 21-30 --size full --validate-world data/world_full11 --validate-db data/harness_full11.db --report reports/calibration-matching.md`.

Calibration: 1551 documents matched by name in born-digital worlds (seeds 21-30), labelled by the generator's ground truth. Method: Learn then Test, fixed-sequence over thresholds from 100 down in steps of 0.5, exact binomial p-values. The margin rule (5.0 points over the runner-up) and the exact-name rule are unchanged. Today's hand-set threshold is 92.0.

| alpha (allowed wrong-attachment rate) | delta | Certified threshold | Accepted at it | Wrong at it |
|---|---|---|---|---|
| 1% | 5% | 77.0 | 1432 | 6 |
| 2% | 5% | 70.0 | 1443 | 17 |
| 5% | 5% | 70.0 | 1443 | 17 |

Validation on a different world with scanned and photographed documents (world_full11, 141 documents matched by name):

| Threshold | Accepted | Share accepted | Wrong attachments |
|---|---|---|---|
| 92.0 (today) | 132 | 93.6% | 0 |
| 77.0 | 132 | 93.6% | 0 |
| 70.0 | 133 | 94.3% | 1 |

## What this does and does not show

- The guarantee holds for documents like the calibration ones. Scans and photographs are a different distribution (OCR changes names), which is why the threshold is checked on a scanned world as well.
- The generator's near-twin names are one model of how names collide; real supplier registers will collide differently. In production the labels come from people's decisions on matches.
- Accepting more by name only automates more if nothing else holds the document back: claims, misfiled uploads and identifiers still apply.
