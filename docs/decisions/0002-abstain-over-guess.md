# 0002: Abstain rather than guess, everywhere

## Context
The buyer relies on a SATISFIED status as evidence. A wrong SATISFIED is invisible until an incident or an audit; a
wrong "not sure" costs a few minutes of a reviewer's time. The two errors are not symmetric, so the engine should not
treat them as if they were.

## Decision
Every stage can say "I cannot decide" and passes that on instead of resolving it optimistically:
- **parsing** rejects ambiguous values (malformed digit grouping, impossible dates) instead of coercing them;
- **reading** reports a field stated twice with different values as a conflict;
- **matching** abstains on ties and near-misses, and names the suppliers the document might belong to;
- **checks** return ABSTAIN when a value is missing, unreadable, contradictory or only close to the expected one;
- **the model tier** abstains when its answer depends on statement order or its quoted evidence is not on the page;
- **status** turns any critical ABSTAIN, any requirement with no evaluable check, and any negative status that an
  unresolved document might change, into NEEDS_REVIEW.

## Consequences
- The harness reports the deferral rate (how much is handed to people) next to the error rates, and a gate caps it,
  so "abstain on everything" cannot pass either.
- Fix hints are attached to FAILs only; an ABSTAIN explains what could not be decided and why.

## Alternatives considered
- *Confidence scores everywhere, one threshold at the end.* Hides where the doubt came from; a reviewer cannot act
  on "0.71".
