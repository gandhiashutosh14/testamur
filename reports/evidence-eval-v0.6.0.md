# Evidence QA across four domains: ground truth and RAGAS

Generated 2026-10-25 02:08 UTC at commit `69d6462` with `testamur evidence report evidence-0.json evidence-1.json evidence-10.json evidence-11.json evidence-12.json evidence-13.json evidence-14.json evidence-15.json evidence-2.json evidence-3.json evidence-4.json evidence-5.json evidence-6.json evidence-7.json evidence-8.json evidence-9.json`, from the runs at commit `69d6462`.

160 tasks from public, expert-labelled datasets, a seeded and stratified sample committed before any model ran on it (`evals/evidence/manifest.json`, digest `9fa7a5d18f6074e9`). The answers: `llama.cpp:Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (fingerprint `gguf:3605803b982cb64a|llama-cpp-python 0.3.36|ctx 8192`), given the hybrid retriever's top 4 passages, its decoding constrained to a JSON contract with citations. The judge for RAGAS: `llama.cpp:Phi-4-mini-instruct-Q4_K_M.gguf` (fingerprint `gguf:88c00229914083cd|llama-cpp-python 0.3.36|ctx 8192`), not the model that answered. Embeddings: `BAAI/bge-small-en-v1.5`. Every model call recorded; a replay reproduces every number below except the timings.

## Right answers

Right by each dataset's own criterion. *As answered*: the model's answer. *Evidence rule*: an answer no verified quote supports is turned into an abstention (for a statement about an NDA, into "not mentioned"), TESTAMUR's rule. A wrong answer is one given and wrong; an abstention is not counted as one.

| Dataset | Domain | Right, as answered | Right, evidence rule | Wrong answers, as answered | Wrong answers, evidence rule | Abstained where right | Answers with a verified quote |
|---|---|---|---|---|---|---|---|
| CUAD (contract clauses) | legal | 20 of 32 | 18 of 32 | 8 | 5 | 14 of 16 | 8 of 14 |
| ContractNLI (NDA statements) | legal | 23 of 32 | 21 of 32 | 3 | 3 | 10 of 10 | 14 of 16 |
| TAT-QA (annual reports) | finance | 22 of 32 | 21 of 32 | 10 | 8 | - | 29 of 32 |
| PubMedQA (biomedical abstracts) | biomedical | 12 of 32 | 12 of 32 | 20 | 19 | - | 31 of 32 |
| QASPER (scientific papers) | science | 9 of 32 | 8 of 32 | 17 | 16 | 5 of 8 | 19 of 21 |
| All |  | 86 of 160 | 80 of 160 | 58 | 51 | 29 of 34 | 101 of 115 |

## Retrieval

Whether the gold evidence is among the top 4 passages (the ones the model sees), and the mean reciprocal rank of its first passage in the top 10, over the answerable tasks. PubMedQA retrieves from the pool of all 500 test abstracts; the others from the task's own document.

| Dataset | BM25 | Dense | Hybrid | Hybrid MRR@10 |
|---|---|---|---|---|
| CUAD (contract clauses) | 16 of 16 | 13 of 16 | 15 of 16 | 0.857 |
| ContractNLI (NDA statements) | 20 of 22 | 22 of 22 | 20 of 22 | 0.742 |
| TAT-QA (annual reports) | 32 of 32 | 32 of 32 | 32 of 32 | 0.956 |
| PubMedQA (biomedical abstracts) | 32 of 32 | 32 of 32 | 32 of 32 | 1.0 |
| QASPER (scientific papers) | 11 of 24 | 15 of 24 | 14 of 24 | 0.452 |
| All | 111 of 126 | 114 of 126 | 113 of 126 | 0.821 |

## RAGAS

Means over the tasks each metric applies to (a metric that needs a reference is not scored without one; a metric that needs an answer, not on an abstention).

| Dataset | Faithfulness | Answer relevancy | Context precision | Context recall | Factual correctness |
|---|---|---|---|---|---|
| CUAD (contract clauses) | 0.958 (n=14) | 0.42 (n=14) | 0.703 (n=16) | 0.969 (n=16) | 0.758 (n=12) |
| ContractNLI (NDA statements) | 0.854 (n=16) | 0.669 (n=16) | 0.898 (n=22) | 0.848 (n=22) | 0.593 (n=16) |
| TAT-QA (annual reports) | 0.646 (n=32) | 0.129 (n=32) | 0.831 (n=32) | 0.906 (n=32) | 0.713 (n=32) |
| PubMedQA (biomedical abstracts) | 0.867 (n=32) | 0.465 (n=32) | 0.868 (n=32) | 0.963 (n=31) | 0.482 (n=32) |
| QASPER (scientific papers) | 0.96 (n=21) | 0.32 (n=21) | 0.597 (n=24) | 0.924 (n=23) | 0.674 (n=17) |
| All | 0.832 (n=115) | 0.368 (n=115) | 0.791 (n=126) | 0.922 (n=124) | 0.626 (n=109) |

## How far the judge can be trusted

AUROC: the probability that a right answer (by the ground truth) scores higher than a wrong one, so 0.5 is a metric that cannot tell them apart. For faithfulness, also against TESTAMUR's deterministic quote check.

| Metric | Scored | AUROC against right answers |
|---|---|---|
| faithfulness | 115 | 0.476 |
| answer relevancy | 115 | 0.525 |
| context precision | 126 | 0.63 |
| context recall | 124 | 0.527 |
| factual correctness | 109 | 0.612 |

Faithfulness against the verified-quote check: AUROC 0.507.

The judge made 1,637 calls (1,494,724 tokens, 51,707 s on the runners' CPUs).

## Cost

Answers: 156,063 tokens; 52.7 s per answer at the median, 97.1 s at the 95th percentile, on one 4-vCPU GitHub runner per shard, no GPU. Model cost: 0 (open weights on the runner's CPU).

## What this does and does not show

- 32 tasks per dataset find where the system goes wrong and roughly how often; they do not rank it against published results, which use full test sets and larger models.
- The sample is stratified (half CUAD's tasks have no answer in the contract, a third of ContractNLI's statements are not mentioned), so the abstention numbers mean something; the proportions are not the datasets' own.
- RAGAS's numbers are a small judge's opinion; the AUROC table says how much of it agrees with the ground truth.
- TAT-QA's numbers are matched with their sign and within 0.5%; QASPER's free answers by token F1, which undercounts paraphrases.

## Sources

- **cuad** (legal, CC BY 4.0): Hendrycks, Burns, Chen and Ball, CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review, NeurIPS 2021 Datasets and Benchmarks.
- **contractnli** (legal, CC BY 4.0): Koreeda and Manning, ContractNLI: A Dataset for Document-level Natural Language Inference for Contracts, Findings of EMNLP 2021.
- **tatqa** (finance, MIT): Zhu, Lei, Huang, Wang, Zhang, Lv, Feng and Chua, TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual Content in Finance, ACL 2021.
- **pubmedqa** (biomedical, MIT): Jin, Dhingra, Liu, Cohen and Lu, PubMedQA: A Dataset for Biomedical Research Question Answering, EMNLP 2019.
- **qasper** (science, CC BY 4.0): Dasigi, Lo, Beltagy, Cohan, Smith and Gardner, A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers, NAACL 2021.
