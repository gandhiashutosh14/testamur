# 0001: Route every check to the cheapest tier that can decide it

## Context
Most questions about a compliance document are cheap to answer exactly: compare two dates, check a number against a
threshold, validate check digits. A few need language judgement (does this clause cap liability?). Language models
are slow and paid per call, and they are wrong in ways rules are not.

## Decision
Each check declares its tier in the domain pack: **metadata** (no reading), **field** (a deterministic rule over a
value read from the page) or **model**. The engine runs tiers in that order. Model checks run only:
- on the *current* document for a requirement (superseded evidence is never judged);
- when no deterministic check has already decided the outcome (a critical failure means the model check is
  SKIPPED, recorded as such, never counted as a pass);
- on documents that carry no text aimed at automated readers.

## Consequences
- Cost scales with the number of contracts whose outcome is still open, not with the number of uploads.
- Moving a check down the ladder (a clause that turns out to have a fixed wording becomes a regex) is a pack change
  with a measurable effect in the harness.
- SKIPPED is a first-class verdict, so "not evaluated" can never be confused with "passed".

## Alternatives considered
- *Ask the model everything and post-check it.* Simpler pipeline, an order of magnitude more cost, and the
  deterministic questions get answered less reliably than by the rules they replace.
- *Model only as a fallback when a field cannot be read.* Kept as an option for the future, measured before use: a
  model reading a value OCR could not read is a new source of false passes.
