# 0004: Deduplicate files per tenant, not globally

## Context
Content addressing stores each file once and lets every later upload of the same bytes reuse its extraction and
judgements. The same file can legitimately reach two tenants (a supplier sends its certificate to two customers).

## Decision
The blob store, the extraction cache and the model-call ledger are keyed by tenant as well as content.

## Consequences
- Two tenants holding the same certificate pay for its OCR twice. That is the price.
- One tenant can never learn, from timing or from a cache hit, that another tenant holds a file; and a judgement made
  in one tenant's context (its suppliers, its engagements) can never leak into another's.

## Alternatives considered
- *Global content store with per-tenant references.* Cheaper at scale; reconsider for public documents (standards,
  published certificates) through an explicit, opt-in shared tier rather than by default.
