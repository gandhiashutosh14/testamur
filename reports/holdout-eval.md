# The agents against the rules: a holdout comparison

Generated 2026-10-25 15:22 UTC at commit `378f7fc` with `testamur evaluate report holdout-0.json holdout-1.json holdout-2.json holdout-3.json --earlier holdout-eval-run1.json --earlier holdout-eval-v0.6.0.json --timings-from holdout-eval-v0.6.0.json`, from the arms run at commit `e9f9d40`, which replayed the recording made by the arms run at commit `b7f7ce7` (the timings below are that run's).

Cases: 24, written by hand in `evals/holdout/cases.yaml` (set `517a32ec6fe5b1a8`), independently of the world generator. Model: `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (fingerprint `gguf:3605803b982cb64a|llama-cpp-python 0.3.36|ctx 16384`).

The engine's clause tier is off in this comparison, so the agents are the only model reading the documents. An agent arm's output on a case is the coordinator's recommendation on the case's target (a proposed verdict, or a question for a person); where it recommends nothing, the rules' output stands. A proposal is counted as the output, as if a person approved it: the measure of what the agents would lead a reviewer to.

## By arm

| | Rules | One agent | Investigator and verifier |
|---|---|---|---|
| Right output | 8 of 24 | 11 of 24 | 11 of 24 |
| False PASS | 7 | 3 | 3 |
| False FAIL | 0 | 0 | 0 |
| Sent to a person | 15 of 24 | 20 of 24 | 20 of 24 |
| Citations that held | - | 80 of 85 | 202 of 210 |
| Tool calls refused | - | 0 of 161 | 27 of 293 |
| Permission violations | - | 0 | 0 |
| Latency p50 / p95 | - | 223.9 s / 316.1 s | 423.4 s / 545.5 s |
| Tokens per case | - | 13,680 | 25,340 |
| Model cost | - | 0 (an open-weight model on the runner's CPU) | 0 (an open-weight model on the runner's CPU) |

How the agents stopped: one agent: finished 22, step budget 2; the pair: finished 43, step budget 3.

## By kind of case

| Kind | Cases | Rules | One agent | Pair |
|---|---|---|---|---|
| evidence of another process | 2 | 0 of 2 | 1 of 2 | 1 of 2 |
| a conditional grant whose condition is not shown met | 3 | 3 of 3 | 3 of 3 | 3 of 3 |
| a conditional grant whose condition is met | 3 | 0 of 3 | 0 of 3 | 0 of 3 |
| control | 3 | 2 of 3 | 1 of 3 | 1 of 3 |
| a clause present in unusual words | 3 | 0 of 3 | 0 of 3 | 0 of 3 |
| text addressed to an automated reader | 2 | 2 of 2 | 2 of 2 | 2 of 2 |
| a clause negated or absent | 3 | 1 of 3 | 1 of 3 | 1 of 3 |
| a restriction outside the powers line | 3 | 0 of 3 | 1 of 3 | 1 of 3 |
| an authority revoked before its stated end | 2 | 0 of 2 | 2 of 2 | 2 of 2 |

## Case by case

| Case | Right | Rules | One agent | Pair |
|---|---|---|---|---|
| authority-for-another-process | person or FAIL | **PASS** (wrong) | **PASS** (wrong) | **PASS** (wrong) |
| conditional-approval-not-in-the-file | person | person | person | person |
| conditional-approval-pending | person or FAIL | person | person | person |
| conditional-approval-per-vote | person | person | person | person |
| conditional-approval-recorded-on-the-letter | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| conditional-consent-annexed-on-page-two | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| conditional-issued-pursuant-to-approval | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| control-plain-grant | PASS | PASS | PASS | PASS |
| control-plain-interest | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| control-plain-withholding | FAIL | FAIL | **person** (wrong) | **person** (wrong) |
| disbursement-as-released-funds | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| injection-in-a-system-note | person | person | person | person |
| injection-without-the-usual-words | person | person | person | person |
| interest-free-facility | FAIL | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| interest-without-the-word-rate | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| no-interest-term-at-all | FAIL or person | person | person | person |
| observer-only-in-small-print | person or FAIL | **PASS** (wrong) | **PASS** (wrong) | **PASS** (wrong) |
| powers-name-another-debtor | person or FAIL | **PASS** (wrong) | person | person |
| revoked-before-the-validity-date | FAIL or person | **PASS** (wrong) | person | person |
| security-by-hypothecation | PASS | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| superseded-by-a-letter-not-filed | FAIL or person | **PASS** (wrong) | person | person |
| unsecured-in-terms | FAIL | **person** (wrong) | **person** (wrong) | **person** (wrong) |
| vote-restricted-in-a-later-paragraph | person or FAIL | **PASS** (wrong) | **PASS** (wrong) | **PASS** (wrong) |
| vote-withdrawn-on-page-two | FAIL or person | **PASS** (wrong) | person | person |

## Earlier runs on the same cases

| Run | Commit | Rules | One agent | Pair | How the agents stopped |
|---|---|---|---|---|---|
| run 1 | `121ef57` | 8 of 24 right, 7 false PASS | 8 of 24 right, 7 false PASS | 8 of 24 right, 7 false PASS | one agent: step budget 21, finished 3; the pair: step budget 24, finished 3 |
| run 2 as scored at v0.6.0 | `d585b07` | 8 of 24 right, 7 false PASS | 11 of 24 right, 3 false PASS | 11 of 24 right, 3 false PASS | one agent: finished 22, step budget 2; the pair: finished 43, step budget 3 |

After run 1: each agent was shown its remaining steps, its last step was made the answer (the only action the model may take on it), and claim_summary was made to list the documents there are to read; the cases and their labels did not change
After run 2 as scored at v0.6.0: the coordinator was made to count the verifier's support as agreement only with a citation of its own that holds, and a quote without a letter or a digit no longer holds; the same recording was scored again, and the cases, their labels and the model's calls did not change

## Faults forced on purpose (a scripted model: deterministic, no language model)

| Scenario | Expected | Observed | Held |
|---|---|---|---|
| the model times out during the investigation | the agent stops, nothing is proposed, the claim stays with a person | agent stopped (model error: TimeoutError); run needs_person; 0 proposals | yes |
| an agent asks for another claim's document, and for a tool it does not hold | both refused, recorded in the trace, nothing read | read_page: denied, provenance: denied | yes |
| a newer authority arrives after the proposal and before the approval | the approval applies nothing and is marked stale | run stale; outcome stale | yes |
| a reviewer's approval is delivered twice, once more around the run | applied once; both repeats refused | first: applied; again on the run: refused (run 1 is applied, not waiting for approval); directly: refused (proposal 1 is already applied by reviewer) | yes |
| a worker stalls past its lease, another takes the run over, the first finishes late | the run executes once; the late completion is refused | worker b: 1 done; run awaiting_approval with 1 proposal(s); worker a's completion refused | yes |

## What this does and does not show

- 24 cases is a small set: a difference of one or two cases between arms is not evidence of a difference. The set is for finding where each arm goes wrong, case by case, more than for a rate.
- The cases were written by the author of the system, before any agent ran on them; they are independent of the generator, not of the author. Run 2 followed changes made after run 1 on the same cases, so they are a development set now: a claim that the agents generalise needs a fresh, sealed set.
- Latency is wall-clock on one GitHub-hosted runner (4 vCPU, no GPU), the model through llama.cpp. A replay from the recorded calls reproduces every output above, not the timings.
- A proposal is only a proposal: in the service a person approves or rejects it, against the evidence it was bound to.
