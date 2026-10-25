# A tour of TESTAMUR, end to end

An hour or two, in order, on one laptop (CPU only). Each stop says what to run, what you will see, what it proves and
where the code is. Stops 4 and 6 can use a local model, or replay recordings made on GitHub's runners instead.

## 0. Set up (10 minutes)

```bash
git clone https://github.com/gandhiashutosh14/testamur && cd testamur
python -m venv .venv && .venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[ocr,agents,service,eval,dev]"
pytest -q
```

**You will see** about three hundred tests pass and four skip with a stated reason (they need a Postgres server; CI
runs them). A run that collects fewer tests than the floor, or skips one without a reason, fails: see
[`tests/conftest.py`](../tests/conftest.py). `python scripts/prove_tests_fire.py` (20 minutes) breaks each safety rule
on purpose, one at a time, and requires the tests to go red every time.

## 1. The engine on a generated world (10 minutes)

```bash
testamur world --seed 11 --size tiny --out data/world_tiny
testamur harness --world data/world_tiny --db data/harness_tiny.db --report data/harness_tiny.md
testamur explain 1 authority --db data/harness_tiny.db --tenant 1
```

**You will see** the documents of a few insolvency processes read (scans and phone photos through OCR), matched to
their creditors and checked; the report's gates (no false vouching, no vote or payout permitted that the truth
withholds, no wrong match); and every check behind one status, each with its evidence: the line of the page it
read, where it read one.
**It proves** the engine against a world with ground truth for every document, field and check.
**Code:** [`engine.py`](../testamur/engine.py), [`checks.py`](../testamur/checks.py), the domain as data in
[`pack.yaml`](../testamur/packs/insolvency_claims/pack.yaml). Read next: [the walkthrough](walkthrough.md).

## 2. People in the loop (5 minutes)

```bash
testamur queue --db data/harness_tiny.db        # what waits for a person; decide one of its lines:
testamur review check <claim> <requirement> <check> PASS --by you@firm --db data/harness_tiny.db
testamur dispatch --db data/harness_tiny.db
```

**It proves** that what the engine cannot decide waits for a person, that a person's decision is an input applied on
every later run, and that findings become tasks, reminders and holds through an outbox.

## 3. The service (10 minutes)

```bash
python scripts/dev_service.py             # http://127.0.0.1:8765; keys in data/dev-service/keys.txt
```

Sign in with the reviewer key; open a claim, its evidence, a document's page (with the line a check read
highlighted); the API at `/docs`. With Docker: `docker compose up --build` starts PostgreSQL (tenant sessions under
row-level security), an S3-compatible store, Jaeger and a worker; `docker compose logs seed` prints the keys, and
http://localhost:16686 shows each request's trace.
**Code:** [`service/`](../testamur/service/), [ADR 0018](decisions/0018-a-service-for-reviewers.md).

## 4. The agents (15 minutes)

Read [ADR 0017](decisions/0017-an-investigator-and-a-verifier.md), then replay the holdout comparison from its
recording (no model needed):

```bash
for i in 0 1 2 3; do testamur evaluate run --shard $i/4 --cassette reports/cassettes/holdout.jsonl --out data/h-$i.json; done
testamur evaluate check data/h-0.json data/h-1.json data/h-2.json data/h-3.json
```

**You will see** every one of the 24 hand-written cases give the output the committed report gives it.
[`reports/holdout-eval.md`](../reports/holdout-eval.md) says what the agents add: they send to a person some of the
PASSes the rules got wrong, and decide nothing on their own; the verifier changed no outcome. With a model
(`--model-path` a GGUF file), `testamur investigate start <claim> --db data/harness_tiny.db` runs them live, and the
review page shows the run, its trace and the proposal you approve against its evidence.

## 5. Evidence QA on public datasets (20 minutes)

```bash
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu   # for a live model
testamur corpus fetch                     # five datasets, about 100 MB, each verified against its checksum
testamur evidence run --limit 2 --no-ragas --embedder hash --model-path Qwen3-4B-Instruct-2507-Q4_K_M.gguf
testamur evidence report artifacts/evidence-0.json --report data/evidence.md --data data/evidence.json
```

**You will see** contract clauses (CUAD), statements about NDAs (ContractNLI), questions over annual reports (TAT-QA),
biomedical abstracts (PubMedQA) and scientific papers (QASPER) answered with quotes checked against their passages,
scored against each dataset's ground truth. Without a model, replay the recording attached to the v0.6.0 release:

```bash
gh release download v0.6.0 --repo gandhiashutosh14/testamur --pattern "evidence-*.jsonl" --dir recording
sha256sum -c evals/evidence/recording.sha256          # the recording is the one the report was made from
testamur evidence run --cassette recording/evidence-answers.jsonl --judge-cassette recording/evidence-judge.jsonl --out data/e.json
testamur evidence check data/e.json                   # every task scored as the committed report scores it
```

[`reports/evidence-eval.md`](../reports/evidence-eval.md) reads in this order: right answers by dataset (as answered,
and under the evidence rule that withholds an answer no quote supports), retrieval (BM25, dense, hybrid), RAGAS, and
how far the RAGAS judge agrees with the ground truth. **Code:** [`evidence/`](../testamur/evidence/),
[ADR 0020](decisions/0020-evidence-qa-on-public-datasets.md). In the service, "Ask this document" on any document
page runs the same pipeline.

## 6. How it stays honest (5 minutes)

Open [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): lint, the tests on two Pythons, the safety rules
broken on purpose, the harness gates replayed from a recording, the queue, isolation and migrations on PostgreSQL, the
holdout replayed, the Compose stack driven as a client, and the pipeline itself checked by zizmor and actionlint; one
check, `gate`, passes only if all of them did. Every number in the README is written from a committed report by
[`scripts/results.py`](../scripts/results.py), and a test fails if the two disagree. An independent review of v0.6
found five gaps; its seven failing tests are in
[`tests/test_v06_independent_review.py`](../tests/test_v06_independent_review.py), passing now, and
[ADR 0021](decisions/0021-the-v06-review.md) says what changed and what the rescoring moved. Read
[how a change ships](delivery.md).

## What to say about it, in five lines

1. A claim-verification engine for India's insolvency process: OCR, matching, rule-first checks, a model only where
   language must be read, and evidence-backed verdicts with the line of the page wherever there is one.
2. Two agents (LangGraph) that read a claim's evidence and can only propose; a person applies a proposal, atomically,
   against the evidence it was bound to.
3. A service shaped for deployment: FastAPI, PostgreSQL row-level security by migration, an object store, traces, a
   Compose stack tested end to end in CI.
4. Measured on documents nobody here wrote: five public datasets in four domains, against their ground truth (with
   adapted scoring) and by RAGAS with a judge of another family, the judge itself measured; an independent review's
   regression tests in the suite, and the recordings scored again after its fixes.
5. Honest about what it does not do: synthetic insolvency data, small samples, simulated payments and votes, and the
   reports say where it fails.
