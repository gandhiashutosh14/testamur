# Shadow OCR trial

Commit `b52caf3`, command `testamur shadow --world data/world_full11 --report reports/shadow-ocr-full-seed11.md`. Candidates ran beside the canonical engine (`rapidocr`, PP-OCRv6 small) on a 40% sample of the scanned and photographed documents of world seed 11, observe-only. Field values read from each engine's text are compared with the ground truth. Only metrics are recorded, never text.

| Candidate | Documents | Pages | Fields | Canonical accuracy | Candidate accuracy | Lost | Gained | s/page canonical | s/page candidate | Cutover allowed |
|---|---|---|---|---|---|---|---|---|---|---|
| fast | 52 | 52 | 185 | 100.0% | 100.0% | 0 | 0 | 1.214 | 0.737 | **yes** |
| en-v4 | 52 | 52 | 185 | 100.0% | 84.9% | 28 | 0 | 1.184 | 1.825 | no |

Gate: the candidate may lose at most 0.5 points of field accuracy and 1% of fields the canonical engine reads correctly, and must be at least 1.2x faster.

- **fast**: passes every condition.
- **en-v4**: fails on accuracy drop (points) (15.14 <= 0.5), share of fields lost (0.1514 <= 0.01), speed-up (0.65 >= 1.2).

Switching is configuration (`TESTAMUR_OCR_VARIANT=<candidate>`); extraction caches are keyed by engine, so a switch back is instant. Timings are this machine's.
