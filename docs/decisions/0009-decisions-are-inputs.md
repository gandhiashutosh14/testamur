# 0009: People's decisions are inputs, bound to the rule they were made under

## Context
The engine abstains by design, so people resolve what it cannot: whose document this is, what an unrecognised
file is, whether a near-identical name is the same company, whether a contract clause says what it should. People
also catch the engine being wrong. Two naive designs both fail:
- **Overwrite the engine's verdict.** The next re-index or engine upgrade recomputes it and the person's work is
  silently undone; and with the engine's own answer gone, nobody can measure how often it was wrong.
- **Keep the decision forever.** When the rule changes (a tier's minimum limit goes up), a person's PASS made under
  the old minimum keeps vouching under a rule they never saw.

## Decision
- **Four kinds of decision**, each a row recording who, when, why and what the engine had said:
  a check verdict (PASS or FAIL), a field reading, a document decision (its type, whose evidence it is, or that it is
  nobody's), and a security clearance for a document that addresses automated readers.
- **Decisions are inputs.** Every document and status is derived from the file plus the decisions about it, so a
  re-index, a new engine version or a recomputation applies them again. Nothing overwrites a decision, and the
  engine's own verdict is kept beside it: that pair is what per-check accuracy is measured from.
- **Prefer correcting the reading.** When the engine misread a value, a person corrects the field and the rules
  judge it, today and under any later rule. A check verdict is for when the reading is right and the judgement
  needs a person.
- **A check verdict is bound to its rule.** Each carries the rule digest: the check's definition plus the pack tables
  its rule reads (a tier table, an allow-list). If either changes, the decision stops applying and the engine judges
  again, which may send the case back to the queue. Rewording a check's hint does not count as a change.
- **Decisions do not open doors the engine keeps shut.** A cleared hostile document may vouch again, but its text
  is still never shown to the model, so its model checks also need a person.
- **A person decides PASS or FAIL.** "I cannot tell either" leaves the item in the queue.
- Every decision writes an audit row; the review queue is derived, never stored, so it cannot drift from the data.

## Consequences
- Review work is never lost and never silently undone; it is undone only by a later decision or a rule change, and
  both are visible.
- A decision re-derives one document from its cached reading: no OCR page and no new model call (model answers come
  from the ledger, since the prompt has not changed).
- Overturning a deciding failure (an "unsigned" contract that was in fact signed) makes the skipped model checks run.
  A skip is therefore derived again on every pass instead of being stored as final.

## Alternatives considered
- *Pin decisions to the engine version.* Every release would discard all review work.
- *Store decisions as edits to the verdict table.* Loses the engine's answer, which is the accuracy signal.
