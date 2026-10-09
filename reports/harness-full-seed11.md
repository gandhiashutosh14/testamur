# Harness report: full world, seed 11

**PASSED**: 9 of 9 gates. Generated 2026-10-09 21:09 UTC at commit `3e51f93` with `testamur harness --world data/world_full11 --db data/harness_full11.db --report reports/harness-full-seed11.md --cassette reports/cassettes/full-seed11.jsonl`.

Engine `0.1.0a1+pack.d0d378356cac`, OCR `1+rapidocr-3.10.0`, model `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (answers replayed from a recording), as-of date 2026-10-01, Python 3.11.9. 378 uploads, 351 distinct documents.

## Gates

| Gate | Value | Limit | Result |
|---|---|---|---|
| status false assurance | 0 | <= 0 | pass |
| critical check false passes | 0 | <= 0 | pass |
| documents matched to the wrong supplier | 0 | <= 0 | pass |
| classification accuracy | 1.0 | >= 0.95 | pass |
| status deferral rate | 0.0171 | <= 0.25 | pass |
| injected documents detected | 1.0 | >= 1.0 | pass |
| model PASS on hostile documents | 0 | <= 0 | pass |
| OCR pages paid again on re-index | 0.0 | <= 0 | pass |
| model calls paid again on re-index | 0.0 | <= 0 | pass |

## Requirement status

585 engagement-requirement pairs. Exact: 571 (97.6%). Deferred to review: 10 (1.7%). Vouching kept, with a non-critical abstention shown as a finding: 4. False assurance (vouching when the truth is otherwise): 0. False alarm: 0. Other mismatch: 0.

| Truth -> engine | Count |
|---|---|
| COVERED_BY_PARENT -> COVERED_BY_PARENT | 6 |
| EXPIRED -> EXPIRED | 15 |
| EXPIRED -> NEEDS_REVIEW | 3 |
| MISSING -> MISSING | 23 |
| MISSING -> NEEDS_REVIEW | 1 |
| NEEDS_REVIEW -> NEEDS_REVIEW | 24 |
| NOT_REQUIRED -> NOT_REQUIRED | 152 |
| NOT_SATISFIED -> NEEDS_REVIEW | 3 |
| NOT_SATISFIED -> NOT_SATISFIED | 79 |
| SATISFIED -> NEEDS_REVIEW | 3 |
| SATISFIED -> SATISFIED | 203 |
| SATISFIED -> SATISFIED_WITH_FINDINGS | 4 |
| SATISFIED_WITH_FINDINGS -> SATISFIED_WITH_FINDINGS | 66 |
| VERIFIED_OUT_OF_BAND -> VERIFIED_OUT_OF_BAND | 3 |

## Checks

By tier (only where the engine and the truth judged the same document):

| Tier | Judgements | Agree | False PASS | Truth negatives | Abstained | False FAIL |
|---|---|---|---|---|---|---|
| metadata | 200 | 200 | 0 | 58 | 0 | 0 |
| field | 1440 | 1440 | 0 | 148 | 0 | 0 |
| model | 188 | 168 | 0 | 43 | 20 | 0 |

| Check | Tier | Severity | n | Agree | False PASS | 95% upper bound on false-PASS rate | Abstained | False FAIL |
|---|---|---|---|---|---|---|---|---|
| bank_channel_verified | metadata | critical | 109 | 109 | 0 | 7.4% | 0 | 0 |
| bank_holder_matches | field | critical | 109 | 109 | 0 | 27.8% | 0 | 0 |
| bank_iban_on_file | field | critical | 109 | 109 | 0 | 25.9% | 0 | 0 |
| bank_iban_valid | field | critical | 109 | 109 | 0 | 35.4% | 0 | 0 |
| bank_letter_recent | field | minor | 109 | 109 | 0 | 24.3% | 0 | 0 |
| clause_anti_assignment | model | minor | 47 | 43 | 0 | 29.9% | 4 | 0 |
| clause_audit_rights | model | major | 47 | 38 | 0 | 21.5% | 9 | 0 |
| clause_data_protection | model | critical | 47 | 44 | 0 | 35.4% | 3 | 0 |
| clause_liability_cap | model | major | 47 | 43 | 0 | 22.8% | 4 | 0 |
| ins_cover_current | field | critical | 91 | 91 | 0 | 17.6% | 0 | 0 |
| ins_issued_recently | field | minor | 91 | 91 | 0 | 20.4% | 0 | 0 |
| ins_limit_meets_tier | field | critical | 91 | 91 | 0 | 49.0% | 0 | 0 |
| ins_name_matches | field | critical | 91 | 91 | 0 | n/a | 0 | 0 |
| ins_renewal_due | field | minor | 91 | 91 | 0 | 17.6% | 0 | 0 |
| msa_signed | field | critical | 47 | 47 | 0 | 49.0% | 0 | 0 |
| received_before_start | metadata | minor | 91 | 91 | 0 | 27.8% | 0 | 0 |
| sec_body_accredited | field | major | 43 | 43 | 0 | 43.4% | 0 | 0 |
| sec_not_expired | field | critical | 43 | 43 | 0 | 43.4% | 0 | 0 |
| sec_org_matches | field | critical | 43 | 43 | 0 | n/a | 0 | 0 |
| sec_scope_covers_service | field | major | 43 | 43 | 0 | 43.4% | 0 | 0 |
| tax_lei_valid | field | major | 110 | 110 | 0 | 32.4% | 0 | 0 |
| tax_name_matches | field | critical | 110 | 110 | 0 | 25.9% | 0 | 0 |
| tax_vat_format | field | major | 110 | 110 | 0 | 20.4% | 0 | 0 |

## Classification and matching

Classification accuracy 100.0% (351 of 351).

| Matching outcome | Documents |
|---|---|
| matched correctly | 327 |
| abstained as expected | 9 |

## Cost and re-index

First pass: 166.2 s, 146 OCR pages, 233 model calls (101813 tokens, 0 s of model time) for 351 documents.
Re-index of every document: 5.7 s, 0 new OCR pages, 0 new model calls (everything answered from the caches).

## Security

14 of 14 documents carrying text aimed at automated readers were flagged; model checks returned PASS on such documents 0 times.

## What this does not show

- The world is synthetic: fictional suppliers and documents rendered from templates, then degraded. It is adversarial by design, but real evidence is messier in ways a generator does not anticipate.
- Model-tier numbers come from synthetic contract clauses, not from real contracts.
- Timings are what this machine measured, not a benchmark.
