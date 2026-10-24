# Harness report: small world, seed 7

**PASSED**: 10 of 10 gates. Generated 2026-10-24 18:51 UTC at commit `f16af51` with `testamur harness --world data/world --db data/harness.db --report reports/harness-small-seed7-linux.md --model-path models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --cassette reports/cassettes/small-seed7-linux.jsonl`.

Engine `0.4.0+code.c03965071d+pack.71025b0fab31`, OCR `2+rapidocr-3.10.0`, model `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (`gguf:3605803b982cb64a|llama-cpp-python 0.3.36|ctx 4096`), as-of date 2026-10-01, Python 3.11.17. 119 uploads, 111 distinct documents.

## Gates

| Gate | Value | Limit | Result |
|---|---|---|---|
| status false vouching | 0 | <= 0 | pass |
| vote or payout permitted when the truth withholds it | 0 | <= 0 | pass |
| critical check false passes | 0 | <= 0 | pass |
| documents matched to the wrong creditor | 0 | <= 0 | pass |
| classification accuracy | 1.0 | >= 0.95 | pass |
| status deferral rate | 0.0207 | <= 0.25 | pass |
| injected documents detected | 1.0 | >= 1.0 | pass |
| model PASS on hostile documents | 0 | <= 0 | pass |
| OCR pages paid again on re-index | 0.0 | <= 0 | pass |
| model calls paid again on re-index | 0.0 | <= 0 | pass |

## Requirement status

145 claim-requirement pairs. Exact: 142 (97.9%). Deferred to review: 3 (2.1%). Vouching kept, with a non-critical abstention shown as a finding: 0. False vouching (vouching when the truth is otherwise): 0. False alarm: 0. Other mismatch: 0.

| Truth -> engine | Count |
|---|---|
| COVERED_BY_TRUSTEE -> COVERED_BY_TRUSTEE | 2 |
| EXPIRED -> EXPIRED | 2 |
| MISSING -> MISSING | 26 |
| MISSING -> NEEDS_REVIEW | 2 |
| NEEDS_REVIEW -> NEEDS_REVIEW | 4 |
| NOT_REQUIRED -> NOT_REQUIRED | 12 |
| NOT_SATISFIED -> NEEDS_REVIEW | 1 |
| NOT_SATISFIED -> NOT_SATISFIED | 14 |
| SATISFIED -> SATISFIED | 52 |
| SATISFIED_WITH_FINDINGS -> SATISFIED_WITH_FINDINGS | 26 |
| VERIFIED_OUT_OF_BAND -> VERIFIED_OUT_OF_BAND | 4 |

## Permissions

58 claim-action pairs (to vote, to be paid), decided apart from whether each document suffices. Exact: 58. Permitted when the truth withholds it: 0. Withheld when the truth permits it (a person is asked instead): 0.

## Checks

By tier (only where the engine and the truth judged the same document):

| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |
|---|---|---|---|---|---|---|
| metadata | 43 | 43 | 0 | 14 | 0 | 0 |
| field | 539 | 539 | 0 | 37 | 0 | 0 |
| model | 80 | 75 | 0 | 6 | 5 | 0 |

| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |
|---|---|---|---|---|---|---|---|---|
| auth_covers_voting | field | major | 22 | 22 | 0 | 79.3% | 0 | 0 |
| auth_creditor_matches | field | critical | 22 | 22 | 0 | n/a | 0 | 0 |
| auth_current | field | critical | 22 | 22 | 0 | 49.0% | 0 | 0 |
| auth_in_effect | field | critical | 22 | 22 | 0 | n/a | 0 | 0 |
| auth_issued_recently | field | minor | 22 | 22 | 0 | 49.0% | 0 | 0 |
| auth_renewal_due | field | minor | 22 | 22 | 0 | 39.0% | 0 | 0 |
| bank_account_on_file | field | critical | 20 | 20 | 0 | 56.2% | 0 | 0 |
| bank_channel_verified | metadata | critical | 20 | 20 | 0 | 27.8% | 0 | 0 |
| bank_holder_matches | field | critical | 20 | 20 | 0 | n/a | 0 | 0 |
| bank_ifsc_valid | field | critical | 20 | 20 | 0 | 56.2% | 0 | 0 |
| bank_letter_recent | field | minor | 20 | 20 | 0 | 79.3% | 0 | 0 |
| charge_amount_covers | field | major | 16 | 16 | 0 | 65.8% | 0 | 0 |
| charge_covers_assets | field | major | 16 | 16 | 0 | 79.3% | 0 | 0 |
| charge_holder_matches | field | critical | 16 | 16 | 0 | n/a | 0 | 0 |
| charge_on_debtor | field | critical | 16 | 16 | 0 | n/a | 0 | 0 |
| charge_registrar_recognised | field | major | 16 | 16 | 0 | n/a | 0 | 0 |
| claim_against_debtor | field | critical | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_dated_after_announcement | field | minor | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_gstin_valid | field | major | 23 | 23 | 0 | 56.2% | 0 | 0 |
| claim_lei_valid | field | major | 23 | 23 | 0 | 65.8% | 0 | 0 |
| claim_name_matches | field | critical | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_not_time_barred | field | critical | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_pan_in_gstin | field | major | 23 | 23 | 0 | n/a | 0 | 0 |
| claim_within_books | field | major | 23 | 23 | 0 | 65.8% | 0 | 0 |
| claim_words_match | field | major | 23 | 23 | 0 | 65.8% | 0 | 0 |
| clause_assignment | model | minor | 20 | 17 | 0 | 56.2% | 3 | 0 |
| clause_disbursement | model | critical | 20 | 20 | 0 | n/a | 0 | 0 |
| clause_interest | model | major | 20 | 19 | 0 | 79.3% | 1 | 0 |
| clause_security | model | critical | 20 | 19 | 0 | 65.8% | 1 | 0 |
| facility_borrower_is_debtor | field | critical | 20 | 20 | 0 | n/a | 0 | 0 |
| facility_signed | field | critical | 20 | 20 | 0 | 56.2% | 0 | 0 |
| received_by_due_date | metadata | minor | 23 | 23 | 0 | 49.0% | 0 | 0 |

## Classification and matching

Classification accuracy 100.0% (111 of 111).

| Matching outcome | Documents |
|---|---|
| matched correctly | 103 |
| abstained as expected | 5 |

## Cost and re-index

First pass: 2575.3 s, 51 OCR pages, 129 model calls (65561 tokens, 2506 s of model time) for 111 documents.
Re-index of every document: 1.7 s, 0 new OCR pages, 0 new model calls (everything answered from the caches).

## Security

2 of 2 documents carrying text aimed at automated readers were flagged; model checks returned PASS on such documents 0 times.

## What this does not show

- The world is synthetic: fictional creditors and documents rendered from templates, then degraded. It is adversarial by design, but real evidence is messier in ways a generator does not anticipate.
- Model-tier numbers come from synthetic contract clauses, not from real contracts.
- Timings are what this machine measured, not a benchmark.
