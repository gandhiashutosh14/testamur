# 0020: Evidence QA measured on public datasets in four domains, against their ground truth and by RAGAS

## Context
Everything TESTAMUR decides rests on one skill: find the evidence in a document, answer a narrow question with words
that are really on the page, or say it cannot. Until v0.6 that skill was measured only on worlds the project
generates, in one domain, and RAGAS-style metrics, the usual way such systems are judged, were not used at all. Two
questions were open: how well does the skill hold up on documents nobody here wrote, outside insolvency, and how far
can an LLM judge's scores be trusted when the ground truth is known.

## Decision
- **Public, expert-labelled datasets** (`testamur/evidence/sources.py`): CUAD (clauses in commercial contracts) and
  ContractNLI (statements an NDA entails, contradicts or does not mention), both legal; TAT-QA (questions over annual
  reports' tables and text), finance; PubMedQA (yes, no or maybe from abstracts), biomedical; QASPER (questions about
  scientific papers, some unanswerable), science. Each is pinned to a revision and a SHA-256, licensed for reuse
  (CC BY 4.0 or MIT), fetched into `data/` and never committed: the texts are their publishers'. Reports name tasks
  by opaque hashes and print only numbers.
- **A sample fixed before any model ran**: 32 tasks per dataset, stratified so that abstaining and every label are
  measured (half of CUAD's tasks have no answer in the contract; a third of ContractNLI's statements are not
  mentioned), drawn by a seeded hash and committed (`evals/evidence/manifest.json`).
- **The pipeline the product uses.** Retrieval by BM25, by meaning (bge-small through fastembed, ONNX on the CPU) and
  hybrid (reciprocal rank fusion); an answer whose decoding is constrained to a JSON contract with citations first;
  every quote checked against its passage with the clause tier's own check; the evidence rule turns an unsupported
  answer into an abstention. The service's "ask this document" (API and review page) runs the same code on a
  document's pages.
- **Ground truth first**: each dataset's own criterion (CUAD's Jaccard >= 0.5 against the expert's clause, the label,
  a number within 0.5% with its sign, token F1), abstention where abstaining is right, retrieval hit rates and mean
  reciprocal rank, each with the evidence rule and without it.
- **RAGAS beside it, with a judge of another family.** RAGAS 0.4's faithfulness, answer relevancy, context precision
  and recall and factual correctness, judged by Phi-4-mini (MIT) while Qwen3-4B answers, so no model grades its own
  words, through TESTAMUR's own model clients: decoding constrained to each response model's schema (so every
  judgement parses) and every call recorded and replayable. No paid API.
- **The judge is itself measured**: for each RAGAS metric, the AUROC against the ground truth's right and wrong
  answers, and for faithfulness, against the deterministic quote check. A score that cannot separate right from wrong
  is reported as such.
- **Measured on GitHub's CPU runners** (measure.yml, suite evidence): models, corpus and embeddings fetched once into
  the cache, sixteen shards, one report.

## Consequences
- The claims the README can make about the skill are measured on other people's documents, with the dangerous error
  (a wrong answer given with confidence) counted apart from the cautious one (an abstention).
- 32 tasks per dataset find where the system fails and roughly how often; they do not rank it against published
  results, which use full test sets and larger models.
- The recordings hold quotes from the datasets, so they live with the run's artifacts (and a release), not in the
  repository: replaying needs them downloaded.

## Alternatives considered
- *RAGAS alone.* Its numbers are a model's opinion; without the ground truth beside them there is no telling a good
  judge from a bad one.
- *A hosted judge (a frontier model by API).* Better judgements, but a paid dependency and calls that cannot be
  recorded and replayed under the project's rules; a small local judge, measured, was preferred.
- *Committing the corpus.* Simpler to run, but the texts are not this project's to vendor, and a hundred megabytes
  of third-party text in the history would outlive any decision to remove them.
