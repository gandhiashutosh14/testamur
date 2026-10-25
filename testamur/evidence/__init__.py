"""Evidence QA: answer a question about documents with a quote that is really on the page, or abstain.

The skill the claim engine is built on, its clause tier and its agents, taken out of the insolvency domain and measured
on public, expert-labelled datasets in four domains (see sources.py): contracts (CUAD, ContractNLI), annual reports
(TAT-QA), biomedical abstracts (PubMedQA) and scientific papers (QASPER). Retrieval (BM25, dense, hybrid), an answer
constrained to a JSON contract with citations, the same quote check as the clause tier, ground-truth metrics per
dataset, and RAGAS with an open-weight judge of another model family (run.py)."""
