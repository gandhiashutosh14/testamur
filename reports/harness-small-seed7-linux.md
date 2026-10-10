# Harness report: small world, seed 7

**PASSED**: 9 of 9 gates. Generated 2026-10-10 13:19 UTC at commit `6b2b49d` with `testamur harness --world data/world --db data/harness.db --report reports/harness-small-seed7-linux.md --model-path models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --cassette reports/cassettes/small-seed7-linux.jsonl`.

Engine `0.3.0+pack.ec1a0adbe9ad`, OCR `2+rapidocr-3.10.0`, model `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf`, as-of date 2026-10-01, Python 3.11.17. 119 uploads, 111 distinct documents.

## Gates

| Gate | Value | Limit | Result |
|---|---|---|---|
| status false vouching | 0 | <= 0 | pass |
| critical check false passes | 0 | <= 0 | pass |
| documents matched to the wrong creditor | 0 | <= 0 | pass |
| classification accuracy | 1.0 | >= 0.95 | pass |
| status deferral rate | 0.024 | <= 0.25 | pass |
| injected documents detected | 1.0 | >= 1.0 | pass |
| model PASS on hostile documents | 0 | <= 0 | pass |
| OCR pages paid again on re-index | 0.0 | <= 0 | pass |
| model calls paid again on re-index | 0.0 | <= 0 | pass |

## Requirement status

125 claim-requirement pairs. Exact: 121 (96.8%). Deferred to review: 3 (2.4%). Vouching kept, with a non-critical abstention shown as a finding: 0. False vouching (vouching when the truth is otherwise): 0. False alarm: 0. Other mismatch: 1.

| Truth -> engine | Count |
|---|---|
| COVERED_BY_TRUSTEE -> COVERED_BY_TRUSTEE | 2 |
| EXPIRED -> EXPIRED | 2 |
| EXPIRED -> NEEDS_REVIEW | 1 |
| MISSING -> MISSING | 9 |
| NEEDS_REVIEW -> NEEDS_REVIEW | 4 |
| NOT_REQUIRED -> NOT_REQUIRED | 11 |
| NOT_SATISFIED -> NEEDS_REVIEW | 2 |
| NOT_SATISFIED -> NOT_SATISFIED | 13 |
| SATISFIED -> SATISFIED | 50 |
| SATISFIED_WITH_FINDINGS -> SATISFIED | 1 |
| SATISFIED_WITH_FINDINGS -> SATISFIED_WITH_FINDINGS | 26 |
| VERIFIED_OUT_OF_BAND -> VERIFIED_OUT_OF_BAND | 4 |

## Checks

By tier (only where the engine and the truth judged the same document):

| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |
|---|---|---|---|---|---|---|
| metadata | 41 | 41 | 0 | 13 | 0 | 0 |
| field | 462 | 462 | 0 | 36 | 0 | 0 |
| model | 84 | 77 | 0 | 8 | 7 | 0 |

| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |
|---|---|---|---|---|---|---|---|---|
| auth_covers_voting | field | major | 22 | 22 | 0 | 79.3% | 0 | 0 |
| auth_creditor_matches | field | critical | 22 | 22 | 0 | n/a | 0 | 0 |
| auth_current | field | critical | 22 | 22 | 0 | 49.0% | 0 | 0 |
| auth_issued_recently | field | minor | 22 | 22 | 0 | 49.0% | 0 | 0 |
| auth_renewal_due | field | minor | 22 | 22 | 0 | 39.0% | 0 | 0 |
| bank_account_on_file | field | critical | 18 | 18 | 0 | 56.2% | 0 | 0 |
| bank_channel_verified | metadata | critical | 18 | 18 | 0 | 29.9% | 0 | 0 |
| bank_holder_matches | field | critical | 18 | 18 | 0 | n/a | 0 | 0 |
| bank_ifsc_valid | field | critical | 18 | 18 | 0 | 65.8% | 0 | 0 |
| bank_letter_recent | field | minor | 18 | 18 | 0 | 79.3% | 0 | 0 |
| charge_amount_covers | field | major | 15 | 15 | 0 | 65.8% | 0 | 0 |
| charge_covers_assets | field | major | 15 | 15 | 0 | 79.3% | 0 | 0 |
| charge_holder_matches | field | critical | 15 | 15 | 0 | n/a | 0 | 0 |
| charge_on_debtor | field | critical | 15 | 15 | 0 | n/a | 0 | 0 |
| charge_registrar_recognised | field | major | 15 | 15 | 0 | n/a | 0 | 0 |
| claim_dated_after_announcement | field | minor | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_gstin_valid | field | major | 23 | 23 | 0 | 56.2% | 0 | 0 |
| claim_lei_valid | field | major | 23 | 23 | 0 | 65.8% | 0 | 0 |
| claim_name_matches | field | critical | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_not_time_barred | field | critical | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_pan_in_gstin | field | major | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_within_books | field | major | 23 | 23 | 0 | 65.8% | 0 | 0 |
| claim_words_match | field | major | 23 | 23 | 0 | 65.8% | 0 | 0 |
| clause_assignment | model | minor | 21 | 18 | 0 | 56.2% | 3 | 0 |
| clause_disbursement | model | critical | 21 | 20 | 0 | 79.3% | 1 | 0 |
| clause_interest | model | major | 21 | 19 | 0 | 65.8% | 2 | 0 |
| clause_security | model | critical | 21 | 20 | 0 | 65.8% | 1 | 0 |
| facility_signed | field | critical | 21 | 21 | 0 | 56.2% | 0 | 0 |
| received_by_due_date | metadata | minor | 23 | 23 | 0 | 49.0% | 0 | 0 |

## Classification and matching

Classification accuracy 100.0% (111 of 111).

| Matching outcome | Documents |
|---|---|
| matched correctly | 104 |
| abstained as expected | 4 |

## Cost and re-index

First pass: 228.5 s, 51 OCR pages, 135 model calls (68423 tokens, 147 s of model time) for 111 documents.
Re-index of every document: 1.9 s, 0 new OCR pages, 0 new model calls (everything answered from the caches).

## Security

2 of 2 documents carrying text aimed at automated readers were flagged; model checks returned PASS on such documents 0 times.

## What this does not show

- The world is synthetic: fictional creditors and documents rendered from templates, then degraded. It is adversarial by design, but real evidence is messier in ways a generator does not anticipate.
- Model-tier numbers come from synthetic contract clauses, not from real contracts.
- Timings are what this machine measured, not a benchmark.
