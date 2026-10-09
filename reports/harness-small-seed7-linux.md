# Harness report: small world, seed 7

**PASSED**: 9 of 9 gates. Generated 2026-10-09 20:37 UTC at commit `85076cb` with `testamur harness --world data/world --db data/harness.db --report artifacts/harness-small-live.md --model-path models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --cassette artifacts/small-seed7-linux.jsonl`.

Engine `0.1.0a1+pack.d0d378356cac`, OCR `1+rapidocr-3.10.0`, model `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf`, as-of date 2026-10-01, Python 3.11.17. 113 uploads, 105 distinct documents.

## Gates

| Gate | Value | Limit | Result |
|---|---|---|---|
| status false assurance | 0 | <= 0 | pass |
| critical check false passes | 0 | <= 0 | pass |
| documents matched to the wrong supplier | 0 | <= 0 | pass |
| classification accuracy | 1.0 | >= 0.95 | pass |
| status deferral rate | 0.027 | <= 0.25 | pass |
| injected documents detected | 1.0 | >= 1.0 | pass |
| model PASS on hostile documents | 0 | <= 0 | pass |
| OCR pages paid again on re-index | 0.0 | <= 0 | pass |
| model calls paid again on re-index | 0.0 | <= 0 | pass |

## Requirement status

185 engagement-requirement pairs. Exact: 180 (97.3%). Deferred to review: 5 (2.7%). False assurance (vouching when the truth is otherwise): 0. False alarm: 0. Other mismatch: 0.

| Truth -> engine | Count |
|---|---|
| COVERED_BY_PARENT -> COVERED_BY_PARENT | 1 |
| EXPIRED -> EXPIRED | 5 |
| MISSING -> MISSING | 14 |
| NEEDS_REVIEW -> NEEDS_REVIEW | 4 |
| NOT_REQUIRED -> NOT_REQUIRED | 49 |
| NOT_SATISFIED -> NEEDS_REVIEW | 1 |
| NOT_SATISFIED -> NOT_SATISFIED | 15 |
| SATISFIED -> NEEDS_REVIEW | 4 |
| SATISFIED -> SATISFIED | 70 |
| SATISFIED_WITH_FINDINGS -> SATISFIED_WITH_FINDINGS | 18 |
| VERIFIED_OUT_OF_BAND -> VERIFIED_OUT_OF_BAND | 4 |

## Checks

By tier (only where the engine and the truth judged the same document):

| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |
|---|---|---|---|---|---|---|
| metadata | 53 | 53 | 0 | 10 | 0 | 0 |
| field | 405 | 405 | 0 | 40 | 0 | 0 |
| model | 56 | 49 | 0 | 5 | 7 | 0 |

| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |
|---|---|---|---|---|---|---|---|---|
| bank_channel_verified | metadata | critical | 29 | 29 | 0 | 35.4% | 0 | 0 |
| bank_holder_matches | field | critical | 29 | 29 | 0 | n/a | 0 | 0 |
| bank_iban_on_file | field | critical | 29 | 29 | 0 | 43.4% | 0 | 0 |
| bank_iban_valid | field | critical | 29 | 29 | 0 | 56.2% | 0 | 0 |
| bank_letter_recent | field | minor | 29 | 29 | 0 | 35.4% | 0 | 0 |
| clause_anti_assignment | model | minor | 14 | 12 | 0 | 79.3% | 2 | 0 |
| clause_audit_rights | model | major | 14 | 13 | 0 | 79.3% | 1 | 0 |
| clause_data_protection | model | critical | 14 | 13 | 0 | 79.3% | 1 | 0 |
| clause_liability_cap | model | major | 14 | 11 | 0 | 65.8% | 3 | 0 |
| ins_cover_current | field | critical | 24 | 24 | 0 | 49.0% | 0 | 0 |
| ins_issued_recently | field | minor | 24 | 24 | 0 | 56.2% | 0 | 0 |
| ins_limit_meets_tier | field | critical | 24 | 24 | 0 | 65.8% | 0 | 0 |
| ins_name_matches | field | critical | 24 | 24 | 0 | n/a | 0 | 0 |
| ins_renewal_due | field | minor | 24 | 24 | 0 | 56.2% | 0 | 0 |
| msa_signed | field | critical | 14 | 14 | 0 | n/a | 0 | 0 |
| received_before_start | metadata | minor | 24 | 24 | 0 | 56.2% | 0 | 0 |
| sec_body_accredited | field | major | 14 | 14 | 0 | 79.3% | 0 | 0 |
| sec_not_expired | field | critical | 14 | 14 | 0 | 56.2% | 0 | 0 |
| sec_org_matches | field | critical | 14 | 14 | 0 | n/a | 0 | 0 |
| sec_scope_covers_service | field | major | 14 | 14 | 0 | 79.3% | 0 | 0 |
| tax_lei_valid | field | major | 33 | 33 | 0 | 56.2% | 0 | 0 |
| tax_name_matches | field | critical | 33 | 33 | 0 | 49.0% | 0 | 0 |
| tax_vat_format | field | major | 33 | 33 | 0 | 79.3% | 0 | 0 |

## Classification and matching

Classification accuracy 100.0% (105 of 105).

| Matching outcome | Documents |
|---|---|
| matched correctly | 96 |
| abstained as expected | 6 |

## Cost and re-index

First pass: 1919.3 s, 54 OCR pages, 93 model calls (41638 tokens, 1848 s of model time) for 105 documents.
Re-index of every document: 1.3 s, 0 new OCR pages, 0 new model calls (everything answered from the caches).

## Security

2 of 2 documents carrying text aimed at automated readers were flagged; model checks returned PASS on such documents 0 times.

## What this does not show

- The world is synthetic: fictional suppliers and documents rendered from templates, then degraded. It is adversarial by design, but real evidence is messier in ways a generator does not anticipate.
- Model-tier numbers come from synthetic contract clauses; expert-labelled real contracts are measured separately.
- Timings are what this machine measured, not a benchmark.
