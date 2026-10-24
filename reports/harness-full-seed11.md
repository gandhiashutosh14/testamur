# Harness report: full world, seed 11

**PASSED**: 10 of 10 gates. Generated 2026-10-24 19:35 UTC at commit `f16af51` with `testamur harness --world data/world_full11 --db data/harness_full11.db --report reports/harness-full-seed11.md --model-path models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --cassette reports/cassettes/full-seed11.jsonl`.

Engine `0.4.0+code.c03965071d+pack.71025b0fab31`, OCR `2+rapidocr-3.10.0`, model `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (`gguf:3605803b982cb64a|llama-cpp-python 0.3.36|ctx 4096`), as-of date 2026-10-01, Python 3.11.17. 412 uploads, 383 distinct documents.

## Gates

| Gate | Value | Limit | Result |
|---|---|---|---|
| status false vouching | 0 | <= 0 | pass |
| vote or payout permitted when the truth withholds it | 0 | <= 0 | pass |
| critical check false passes | 0 | <= 0 | pass |
| documents matched to the wrong creditor | 0 | <= 0 | pass |
| classification accuracy | 1.0 | >= 0.95 | pass |
| status deferral rate | 0.0391 | <= 0.25 | pass |
| injected documents detected | 1.0 | >= 1.0 | pass |
| model PASS on hostile documents | 0 | <= 0 | pass |
| OCR pages paid again on re-index | 0.0 | <= 0 | pass |
| model calls paid again on re-index | 0.0 | <= 0 | pass |

## Requirement status

460 claim-requirement pairs. Exact: 441 (95.9%). Deferred to review: 18 (3.9%). Vouching kept, with a non-critical abstention shown as a finding: 1. False vouching (vouching when the truth is otherwise): 0. False alarm: 0. Other mismatch: 0.

| Truth -> engine | Count |
|---|---|
| COVERED_BY_TRUSTEE -> COVERED_BY_TRUSTEE | 3 |
| EXPIRED -> EXPIRED | 4 |
| EXPIRED -> NEEDS_REVIEW | 1 |
| MISSING -> MISSING | 48 |
| MISSING -> NEEDS_REVIEW | 10 |
| NEEDS_REVIEW -> NEEDS_REVIEW | 27 |
| NOT_REQUIRED -> NOT_REQUIRED | 51 |
| NOT_SATISFIED -> NEEDS_REVIEW | 6 |
| NOT_SATISFIED -> NOT_SATISFIED | 74 |
| SATISFIED -> NEEDS_REVIEW | 1 |
| SATISFIED -> SATISFIED | 141 |
| SATISFIED -> SATISFIED_WITH_FINDINGS | 1 |
| SATISFIED_WITH_FINDINGS -> SATISFIED_WITH_FINDINGS | 87 |
| VERIFIED_OUT_OF_BAND -> VERIFIED_OUT_OF_BAND | 6 |

## Permissions

184 claim-action pairs (to vote, to be paid), decided apart from whether each document suffices. Exact: 184. Permitted when the truth withholds it: 0. Withheld when the truth permits it (a person is asked instead): 0.

## Checks

By tier (only where the engine and the truth judged the same document):

| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |
|---|---|---|---|---|---|---|
| metadata | 166 | 166 | 0 | 56 | 0 | 0 |
| field | 1856 | 1854 | 0 | 135 | 2 | 0 |
| model | 260 | 238 | 1 | 71 | 21 | 0 |

| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |
|---|---|---|---|---|---|---|---|---|
| auth_covers_voting | field | major | 77 | 77 | 0 | 56.2% | 0 | 0 |
| auth_creditor_matches | field | critical | 77 | 77 | 0 | n/a | 0 | 0 |
| auth_current | field | critical | 77 | 77 | 0 | 27.8% | 0 | 0 |
| auth_in_effect | field | critical | 77 | 77 | 0 | n/a | 0 | 0 |
| auth_issued_recently | field | minor | 77 | 77 | 0 | 29.9% | 0 | 0 |
| auth_renewal_due | field | minor | 77 | 77 | 0 | 35.4% | 0 | 0 |
| bank_account_on_file | field | critical | 83 | 83 | 0 | 35.4% | 0 | 0 |
| bank_channel_verified | metadata | critical | 83 | 83 | 0 | 10.2% | 0 | 0 |
| bank_holder_matches | field | critical | 83 | 83 | 0 | 24.3% | 0 | 0 |
| bank_ifsc_valid | field | critical | 83 | 83 | 0 | 27.8% | 0 | 0 |
| bank_letter_recent | field | minor | 83 | 83 | 0 | 35.4% | 0 | 0 |
| charge_amount_covers | field | major | 37 | 37 | 0 | 65.8% | 0 | 0 |
| charge_covers_assets | field | major | 37 | 37 | 0 | 43.4% | 0 | 0 |
| charge_holder_matches | field | critical | 37 | 37 | 0 | n/a | 0 | 0 |
| charge_on_debtor | field | critical | 37 | 37 | 0 | 49.0% | 0 | 0 |
| charge_registrar_recognised | field | major | 37 | 37 | 0 | 56.2% | 0 | 0 |
| claim_against_debtor | field | critical | 83 | 83 | 0 | n/a | 0 | 0 |
| claim_dated_after_announcement | field | minor | 83 | 83 | 0 | 39.0% | 0 | 0 |
| claim_gstin_valid | field | major | 83 | 83 | 0 | 39.0% | 0 | 0 |
| claim_lei_valid | field | major | 83 | 83 | 0 | 43.4% | 0 | 0 |
| claim_name_matches | field | critical | 83 | 83 | 0 | 43.4% | 0 | 0 |
| claim_not_time_barred | field | critical | 83 | 83 | 0 | 43.4% | 0 | 0 |
| claim_pan_in_gstin | field | major | 83 | 83 | 0 | 49.0% | 0 | 0 |
| claim_within_books | field | major | 83 | 82 | 0 | 24.3% | 1 | 0 |
| claim_words_match | field | major | 83 | 82 | 0 | 43.4% | 1 | 0 |
| clause_assignment | model | minor | 65 | 56 | 0 | 13.8% | 9 | 0 |
| clause_disbursement | model | critical | 65 | 62 | 0 | 16.8% | 3 | 0 |
| clause_interest | model | major | 65 | 59 | 1 | 25.8% | 5 | 0 |
| clause_security | model | critical | 65 | 61 | 0 | 27.8% | 4 | 0 |
| facility_borrower_is_debtor | field | critical | 65 | 65 | 0 | n/a | 0 | 0 |
| facility_signed | field | critical | 65 | 65 | 0 | 32.4% | 0 | 0 |
| received_by_due_date | metadata | minor | 83 | 83 | 0 | 14.9% | 0 | 0 |

## Classification and matching

Classification accuracy 100.0% (383 of 383).

| Matching outcome | Documents |
|---|---|
| matched correctly | 349 |
| abstained as expected | 16 |

## Cost and re-index

First pass: 5183.4 s, 175 OCR pages, 349 model calls (171018 tokens, 5030 s of model time) for 383 documents.
Re-index of every document: 4.3 s, 0 new OCR pages, 0 new model calls (everything answered from the caches).

## Security

18 of 18 documents carrying text aimed at automated readers were flagged; model checks returned PASS on such documents 0 times.

## What this does not show

- The world is synthetic: fictional creditors and documents rendered from templates, then degraded. It is adversarial by design, but real evidence is messier in ways a generator does not anticipate.
- Model-tier numbers come from synthetic contract clauses, not from real contracts.
- Timings are what this machine measured, not a benchmark.
