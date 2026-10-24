# 0015: Evidence belongs to the process it was filed in

## Context
A document was one row per distinct file per tenant, and a requirement's current document was chosen by document type
and creditor. Nothing tied a proof of claim to the insolvency process it was filed in. An external review created a
claim in a new process for a creditor already satisfied in another, with no upload at all in the new process, and the
new claim's proof of claim read SATISFIED, on the other process's Form C. Creditors that lend to several companies
under one insolvency firm are the ordinary case, not an edge one; the generated world had one claim per creditor, so
the benchmark could not see it, and its ground truth made the same creditor-level choice.

## Decision
- **A document is a file as evidence in one process**: one row per (tenant, matter, file). The bytes and their
  reading are still stored and paid for once per tenant (blobs and extractions are content-addressed); whose
  evidence the file is, and for which claims it counts, is decided in the process it was filed in. The same bytes
  filed in two processes are two documents, each judged against its own process's dates and debtor.
- **Scope is declared in the pack, per document type.** `evidence_scope: matter` (the default) counts only in the
  process: a proof of claim, an authority for this process, a charge over this debtor's assets, a loan to this
  debtor. `evidence_scope: creditor` counts for every claim of the creditor in the firm, and only the bank account
  confirmation is declared so. Sharing across processes is a written decision, never a default.
- **Matching respects it.** A case document whose identifiers name a creditor with no claim in the process it was
  filed in (nor a holder it acts for) is possibly misfiled: a person says where it belongs. It is never carried to the
  creditor's other process because the creditor matched.
- **Provenance respects it.** How and when a case document arrived (received by the last date for claims, the
  channel) is read from its uploads in its own process.
- **The content is checked too.** A proof of claim states the corporate debtor ("In the matter of") and a loan
  agreement its borrower; both are now critical checks against the process's debtor, beside the charge's.
- **Attestations name the claim** for evidence of one process; one naming only the creditor vouches for creditor-wide
  evidence only.
- **The assistant follows it**: a question can name the process by its debtor, and a creditor with current documents
  in two processes is asked which one is meant.
- **The ground truth follows it, independently**: `synth/truth.py` builds the evidence of each process from the
  generator's own record of where each upload was filed, and the generator now gives some creditors a second claim
  in another process with no evidence of its own (drawn from a separate random stream, so every other document is
  byte-identical and recorded model answers still apply).

## Consequences
- A misfiled case document found by its identifiers is now a question for a person instead of evidence placed
  automatically: a few more deferrals, in exchange for never deciding one process on another's papers.
- A person who finds a misfiled document rejects it where it was filed; filing it in the right process is a new
  upload there. A "refile" decision that carries its arrival time across is future work.

## Alternatives considered
- *Keep one document per file and add an evidence-link table.* Equivalent in what it can express, with one more join
  on every path; the per-process document keeps the engine's unit of judgement and its decisions (a person's reading,
  assignment or security clearance) in one place, and the content-addressed layers below still deduplicate.
- *Filter by matter only in `_current`.* Leaves provenance, attestations, matching and the assistant creditor-wide:
  the same bug in four other places.
