# 0008: Change an extraction engine only after a shadow trial passes a parity gate

## Context
OCR is the largest cost per document, and cheaper or faster engines appear constantly. Swapping one in on faith
risks the worst kind of regression: values read slightly wrong, which the checks then judge confidently.

## Decision
A candidate engine runs beside the canonical one on a sample of the pages that need OCR, observe-only: its output
never reaches a verdict. Both outputs are read into fields and compared with the truth (in production, with the
values reviewers confirmed). A parity gate allows the switch only if the candidate:
- loses at most half a point of field accuracy,
- gets wrong at most 1% of the fields the canonical engine reads correctly, and
- is at least 1.2x faster.

The switch is configuration (`TESTAMUR_OCR_VARIANT`). Extraction caches are keyed by engine and version, so the
two never mix and switching back is instant. The trial records metrics only, never document text.

## Consequences
- The first trial shows the gate working both ways: a configuration that skips the text-angle classifier was as
  accurate and faster (allowed); an older English-only recogniser lost fields and was slower (refused).
- A shadow trial costs a second OCR pass on the sample, paid once per candidate.

## Alternatives considered
- *Benchmark on a public OCR dataset.* Useful, but it measures characters, not the fields that decide verdicts.
