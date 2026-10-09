# 0013: The assistant answers from the engine's records and cites only what is on the page

## Context
Staff will ask about suppliers in their own words: is this one insured, when does it lapse, what is wrong with its
bank letter, which certificates run out this quarter, does the contract let us audit. A conversational assistant over
the documents is the obvious build, and it brings the obvious failures with it: an answer from last year's
certificate, an answer about the wrong one of two similar suppliers, an answer from another customer's data, a
confident answer the documents do not support, and a document whose planted text steers the answer.

## Decision
- **Answer from the engine's records first.** Standing, dates, limits, insurers, certification bodies and findings
  are already read, matched and judged by the engine. The assistant answers those from its statuses, verdicts and
  field readings, so it can be no more wrong than the engine, and it inherits every decision people made.
- **Filters before ranking.** The tenant is fixed by the session; the supplier is resolved by name inside the
  tenant before anything is read; only the current document behind each requirement is used. A superseded
  certificate is never a candidate, so it cannot be cited.
- **Names are taken whole.** The supplier is the capitalised name in the question, compared as a whole with each
  legal name: "Glenmore Facilities Ltd" never becomes "Glenmore Holdings plc". A shared first word ("Glenmore") is
  asked back; a name no supplier of this tenant has is "not found", whoever else may have it.
- **Every quote is on the page.** A citation is given only if its text is found on the cited page of the cited
  document (a person's corrected reading is cited as theirs). Nothing else is said.
- **Unsettled is said, not smoothed over.** When the engine could not settle a value (a certificate stating two
  expiry dates, a document not yet matched), the answer says so and shows both readings; when a newer document may
  be waiting, an answer from the accepted evidence carries that warning.
- **Documents are data.** Free-text questions use hybrid retrieval (BM25 on words, character trigrams for OCR
  slips, fused by reciprocal rank) over the current documents' passages; passages that address automated readers
  are never candidates, and an answer drawn from such a document warns about it.
- **No model in the answer path, yet.** The questions above need none. A generative layer can be added behind the
  same filters and the same citation check: it could phrase, but not assert anything the check does not find.

## Consequences
- The golden set (`testamur golden`) is built from the world's truth with traps: superseded certificates, near-twin
  names, shared first words, another tenant's suppliers, unanswerable questions, hostile documents and expiry lists.
  It measures right answers, wrong values, wrong versions, wrong entities and citations outside the tenant.
- Questions the routing rules do not recognise fall through to retrieval and come back as a quoted passage or as
  "cannot find"; they do not come back invented.

## Alternatives considered
- *Retrieval-augmented generation over all documents.* Version and entity mistakes become the model's to make, and
  every answer needs a model call.
- *Filtering after ranking.* The top results can then all be the wrong supplier's or last year's, and the filter
  leaves nothing.
