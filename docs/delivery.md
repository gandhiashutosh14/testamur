# How a change ships

A wrong verdict here has consequences: the committee of creditors relies on SATISFIED. So a change does not merge because
it looks right; it merges because it has passed checks built to catch the ways it could be wrong, including the
ways an agent that wrote it could be wrong.

## The gates

| Gate | What it protects | Where |
|---|---|---|
| Lint | style and obvious mistakes | `ruff check` |
| Tests | behaviour; a skipped test fails the run unless allow-listed with a reason; a full run that collects fewer tests than the floor fails | `pytest`, [`tests/conftest.py`](../tests/conftest.py) |
| Mutation proof | that the safety tests can fail: each hand-picked safety rule (98 at v0.5) is broken on purpose, one at a time, and the suite must go red for each. A proof that those tests fire, not a mutation score for the codebase | [`scripts/prove_tests_fire.py`](../scripts/prove_tests_fire.py) |
| Rule versions | that a check rule whose code changed has a new version, so people's decisions made under the old meaning stop applying | [`tests/test_rule_versions.py`](../tests/test_rule_versions.py) |
| Harness | the dangerous directions, on a generated world with ground truth: no false vouching, no vote or payout permitted when the truth withholds it, no critical false PASS, no wrong match, classification at least 95%, deferral at most 25%, every planted instruction detected, no model PASS on a hostile document, nothing paid twice on re-index | [`gates.yaml`](../gates.yaml) |
| Recorded model answers | the clause tier runs in CI from answers recorded on the CI platform; an answer that was not recorded fails the job instead of passing silently | `reports/cassettes/` |
| Golden set | the assistant: right answers, no wrong value, version, entity or tenant | `testamur golden` |
| Migrations | that an empty database migrated to head equals the models, comes back down, and on Postgres isolates tenants by row-level security from the migrations alone | [`tests/test_migrations.py`](../tests/test_migrations.py), the Postgres job |
| The holdout, replayed | that every case of the agents' comparison gives the output committed in its report, the model answered from the recording made on the CI platform (a call it does not hold fails) | [`reports/holdout-eval.md`](../reports/holdout-eval.md), the `holdout` job |
| The stack | that the Compose stack builds and starts, and serves a client end to end: readiness at the schema head, an upload read by a worker, an investigation executed through its checkpoints, another firm seeing nothing, traces reaching Jaeger | [`scripts/smoke_stack.py`](../scripts/smoke_stack.py), the `stack` job |
| The pipeline itself | actions pinned to commit hashes, no workflow input expanded into a shell, no credentials left on disk, valid workflow syntax | zizmor, actionlint |
| One required check | `gate` runs always and passes only if every job above succeeded; a skipped job would otherwise report success to a required check | [`ci.yml`](../.github/workflows/ci.yml) |

Branch protection should require `gate` and code-owner review; those are repository settings, not code, and are
listed here so they are not forgotten. As of v0.6.1 they are still not enforced on `main` (two external reviews
checked): until they are, `gate` is the check a merge is held to by practice, not by the platform. On a repository
with one maintainer an approving review cannot come from the author, so the `risk` check's hold on a high-risk
change is released by the maintainer's merge, not by GitHub: pull requests 1 to 4 were merged that way, every other
check green. A red `risk` check released by its author is not an independent review.

## Risk and review

[`scripts/risk.py`](../scripts/risk.py) classifies every pull request by the paths it touches. High risk: what
decides what the platform vouches for or whom it shows what (status policy, check rules, reading values, matching,
verdicts and decisions, tenant isolation, accuracy and the breaker, holds and webhooks, the agent surface, the
assistant, spend caps, the domain pack) and what keeps those honest (the gates, the mutation proof, test discipline,
the pipeline, this classification). A high-risk pull request is labelled, needs an approving review, and the check
fails while auto-merge is on. The classifier runs from the base branch, so a pull request cannot rewrite the rules
it is judged by. [`CODEOWNERS`](../.github/CODEOWNERS) names the same paths.

## Measurements

Every report states the commit and the exact command that produced it. Reports are regenerated, never edited:
measure from a clean checkout of the commit (`git worktree add`), so a result cannot depend on uncommitted code.
A stamp ending `+dirty` means code differed from the commit; rewriting earlier reports does not count.

## Models

Only open-weight models, on CPU: Qwen3-4B-Instruct (Apache-2.0, 4-bit GGUF, llama.cpp) judges contract clauses, and a
model may break a tie in classification. Temperature 0 and a fixed seed, and every answer recorded with the exact
prompt, so CI never calls a model and every run is reproducible. A model can only abstain or agree with evidence it
quotes (ADR 0005); it never sees a document that addresses automated readers. Re-recording is a manual workflow on
the CI platform (`record-cassette`), because floating-point results differ between machines.

## When an agent writes the change

Coding agents are useful on a codebase like this one, and they fail in characteristic ways. The gates assume them:

| What an agent may do | What stops it |
|---|---|
| Weaken a test until it passes | the mutation proof: the weakened test no longer catches its mutant |
| Skip or delete a failing test | the skip rule and the test floor |
| Edit a report to show the expected number | reports are regenerated from a clean checkout and stamped |
| "Fix" the status policy or a rule to make a case pass | high-risk label, code-owner review, and the harness gates on false vouching |
| Follow instructions planted in a document it was asked to process | at run time, agents only propose (ADR 0014); documents that address automated readers never reach a model |
| Pull in a dependency or an action at a moving version | versions pinned; actions pinned to commit hashes; zizmor |
