# Shadow OCR trial

Commit `6b2b49d`, command `testamur shadow --world data/world_full11 --report reports/shadow-ocr-full-seed11.md`. Candidates ran beside the canonical engine (`rapidocr`, PP-OCRv6 small) on a 40% sample of the scanned and photographed documents of world seed 11, observe-only. Field values read from each engine's text are compared with the ground truth. Only metrics are recorded, never text.

| Candidate | Documents | Pages | Fields | Canonical accuracy | Candidate accuracy | Lost | Gained | s/page canonical | s/page candidate | Cutover allowed |
|---|---|---|---|---|---|---|---|---|---|---|
| fast | 64 | 66 | 329 | 99.7% | 99.7% | 0 | 0 | 1.83 | 0.965 | **yes** |
| en-v4 | 64 | 66 | 329 | 99.7% | 85.4% | 47 | 0 | 1.812 | 1.444 | no |

Gate: the candidate may lose at most 0.5 points of field accuracy and 1% of fields the canonical engine reads correctly, and must be at least 1.2x faster.

- **fast**: passes every condition.
- **en-v4**: fails on accuracy drop (points) (14.29 <= 0.5), share of fields lost (0.1429 <= 0.01).

Switching is configuration (`TESTAMUR_OCR_VARIANT=<candidate>`); extraction caches are keyed by engine, so a switch back is instant. Timings are this machine's.
