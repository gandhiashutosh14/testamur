# 0006: Measure on a generated adversarial world with ground truth

## Context
Real claim files are confidential, and nobody publishes thousands of proofs of claim, authorities and charge
certificates labelled with whether each one satisfies a given claim. Without ground truth there is no false-pass rate, and without a
false-pass rate there is no safety claim.

## Decision
A seeded generator builds a world of fictional tenants, matters and creditors, renders their documents as
born-digital PDFs, degraded scans and phone photos, and records the truth: every document's type, owner and field
values, and every check's and requirement's correct outcome. The world is adversarial on purpose (decoy pages,
contradicting amendments, near-twin creditor names, debenture trustees, changed bank details, planted instructions,
duplicates, misfiled uploads, documents of no known type).

The truth is computed from what the generator wrote, by code independent of the engine. The status policy is shared
deliberately: it is the specification under test and has its own unit tests.

## Consequences
- Every number in the reports has a denominator and a seed; anyone can regenerate the world and rerun.
- The generator encodes its author's idea of messiness; real documents will be messier in ways it does not
  anticipate. The reports say so. Contract clauses are therefore also measured on expert-labelled public contracts.
- CI uses a fixed seed with recorded model calls; other seeds are for checking that the engine was not tuned to one
  world.
