# Harness report: full world, seed 11

**PASSED**: 9 of 9 gates. Generated 2026-10-10 13:50 UTC at commit `6b2b49d` with `testamur harness --world data/world_full11 --db data/harness_full11.db --report reports/harness-full-seed11.md --model-path models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --cassette reports/cassettes/full-seed11.jsonl`.

Engine `0.3.0+pack.ec1a0adbe9ad`, OCR `2+rapidocr-3.10.0`, model `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf`, as-of date 2026-10-01, Python 3.11.17. 412 uploads, 382 distinct documents.

## Gates

| Gate | Value | Limit | Result |
|---|---|---|---|
| status false vouching | 0 | <= 0 | pass |
| critical check false passes | 0 | <= 0 | pass |
| documents matched to the wrong creditor | 0 | <= 0 | pass |
| classification accuracy | 1.0 | >= 0.95 | pass |
| status deferral rate | 0.0326 | <= 0.25 | pass |
| injected documents detected | 1.0 | >= 1.0 | pass |
| model PASS on hostile documents | 0 | <= 0 | pass |
| OCR pages paid again on re-index | 0.0 | <= 0 | pass |
| model calls paid again on re-index | 0.0 | <= 0 | pass |

## Requirement status

430 claim-requirement pairs. Exact: 415 (96.5%). Deferred to review: 14 (3.3%). Vouching kept, with a non-critical abstention shown as a finding: 1. False vouching (vouching when the truth is otherwise): 0. False alarm: 0. Other mismatch: 0.

| Truth -> engine | Count |
|---|---|
| COVERED_BY_TRUSTEE -> COVERED_BY_TRUSTEE | 3 |
| EXPIRED -> EXPIRED | 4 |
| MISSING -> MISSING | 26 |
| MISSING -> NEEDS_REVIEW | 1 |
| NEEDS_REVIEW -> NEEDS_REVIEW | 24 |
| NOT_REQUIRED -> NOT_REQUIRED | 49 |
| NOT_SATISFIED -> NEEDS_REVIEW | 6 |
| NOT_SATISFIED -> NOT_SATISFIED | 74 |
| SATISFIED -> NEEDS_REVIEW | 4 |
| SATISFIED -> SATISFIED | 141 |
| SATISFIED -> SATISFIED_WITH_FINDINGS | 1 |
| SATISFIED_WITH_FINDINGS -> NEEDS_REVIEW | 3 |
| SATISFIED_WITH_FINDINGS -> SATISFIED_WITH_FINDINGS | 89 |
| VERIFIED_OUT_OF_BAND -> VERIFIED_OUT_OF_BAND | 5 |

## Checks

By tier (only where the engine and the truth judged the same document):

| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |
|---|---|---|---|---|---|---|
| metadata | 162 | 162 | 0 | 53 | 0 | 0 |
| field | 1612 | 1610 | 0 | 130 | 2 | 0 |
| model | 272 | 249 | 1 | 76 | 22 | 0 |

| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |
|---|---|---|---|---|---|---|---|---|
| auth_covers_voting | field | major | 76 | 76 | 0 | 56.2% | 0 | 0 |
| auth_creditor_matches | field | critical | 76 | 76 | 0 | n/a | 0 | 0 |
| auth_current | field | critical | 76 | 76 | 0 | 29.9% | 0 | 0 |
| auth_issued_recently | field | minor | 76 | 76 | 0 | 32.4% | 0 | 0 |
| auth_renewal_due | field | minor | 76 | 76 | 0 | 35.4% | 0 | 0 |
| bank_account_on_file | field | critical | 78 | 78 | 0 | 35.4% | 0 | 0 |
| bank_channel_verified | metadata | critical | 78 | 78 | 0 | 11.0% | 0 | 0 |
| bank_holder_matches | field | critical | 78 | 78 | 0 | 27.8% | 0 | 0 |
| bank_ifsc_valid | field | critical | 78 | 78 | 0 | 29.9% | 0 | 0 |
| bank_letter_recent | field | minor | 78 | 78 | 0 | 35.4% | 0 | 0 |
| charge_amount_covers | field | major | 36 | 36 | 0 | 65.8% | 0 | 0 |
| charge_covers_assets | field | major | 36 | 36 | 0 | 43.4% | 0 | 0 |
| charge_holder_matches | field | critical | 36 | 36 | 0 | n/a | 0 | 0 |
| charge_on_debtor | field | critical | 36 | 36 | 0 | 56.2% | 0 | 0 |
| charge_registrar_recognised | field | major | 36 | 36 | 0 | 56.2% | 0 | 0 |
| claim_dated_after_announcement | field | minor | 84 | 84 | 0 | 39.0% | 0 | 0 |
| claim_gstin_valid | field | major | 84 | 84 | 0 | 39.0% | 0 | 0 |
| claim_lei_valid | field | major | 84 | 84 | 0 | 43.4% | 0 | 0 |
| claim_name_matches | field | critical | 84 | 84 | 0 | 43.4% | 0 | 0 |
| claim_not_time_barred | field | critical | 84 | 84 | 0 | 43.4% | 0 | 0 |
| claim_pan_in_gstin | field | major | 84 | 84 | 0 | 49.0% | 0 | 0 |
| claim_within_books | field | major | 84 | 83 | 0 | 22.8% | 1 | 0 |
| claim_words_match | field | major | 84 | 83 | 0 | 43.4% | 1 | 0 |
| clause_assignment | model | minor | 68 | 58 | 0 | 12.9% | 10 | 0 |
| clause_disbursement | model | critical | 68 | 65 | 0 | 16.1% | 3 | 0 |
| clause_interest | model | major | 68 | 62 | 1 | 24.6% | 5 | 0 |
| clause_security | model | critical | 68 | 64 | 0 | 25.9% | 4 | 0 |
| facility_signed | field | critical | 68 | 68 | 0 | 32.4% | 0 | 0 |
| received_by_due_date | metadata | minor | 84 | 84 | 0 | 14.9% | 0 | 0 |

## Classification and matching

Classification accuracy 100.0% (382 of 382).

| Matching outcome | Documents |
|---|---|
| matched correctly | 351 |
| abstained as expected | 13 |

## Cost and re-index

First pass: 2040.5 s, 175 OCR pages, 365 model calls (179148 tokens, 1748 s of model time) for 382 documents.
Re-index of every document: 5.9 s, 0 new OCR pages, 0 new model calls (everything answered from the caches).

## Security

18 of 18 documents carrying text aimed at automated readers were flagged; model checks returned PASS on such documents 0 times.

## What this does not show

- The world is synthetic: fictional creditors and documents rendered from templates, then degraded. It is adversarial by design, but real evidence is messier in ways a generator does not anticipate.
- Model-tier numbers come from synthetic contract clauses, not from real contracts.
- Timings are what this machine measured, not a benchmark.
