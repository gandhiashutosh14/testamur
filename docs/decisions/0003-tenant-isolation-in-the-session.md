# 0003: Enforce tenant isolation in the session, not at call sites

## Context
Multi-tenant leaks rarely come from the main request path. They come from the places nobody remembers: a background
job, a report, a new tool for agents, a quick query added during an incident. Each needs the tenant filter, and one
forgotten filter is enough.

## Decision
- Every tenant-owned table inherits a `TenantScoped` mixin with `tenant_id`.
- `Store.tenant(id)` opens a session whose `do_orm_execute` hook adds `tenant_id = id` to every ORM read, including
  primary-key lookups, and whose `before_flush` hook refuses any row of another tenant.
- A session with no tenant cannot read tenant data: the hook raises (fail closed).
- `Store.system()` is the only cross-tenant session; it is used to load the registry and by the harness, and says so
  at the call site.
- Work items carry their tenant explicitly; `Store.tenant(None)` raises, so a job that lost its tenant stops.

## Consequences
- New code is isolated by default; the tests try to break isolation (reads, primary-key gets, cross-tenant writes,
  tenant-less sessions), and the mutation proof removes the filter to show those tests catch it.
- On Postgres the same models can add row-level security as a second, database-side layer.

## Revised in v0.4, after an external review
The review found the hole this record was written to prevent, in a place nobody remembered. The read hook returned
early for anything but a SELECT, and the flush hook only sees objects added to the session, so a bulk
`session.execute(update(...))` or `delete(...)` ran unfiltered: a tenant-1 session could rename tenant 2's creditor.
And a real engine path did it: finishing an OCR read deleted the reading lock by file hash alone, which removed another
tenant's lock on the same bytes (two firms can hold the same file) and let a third worker pay for the same OCR again.

Now:
- The session hook scopes every ORM statement, bulk UPDATE and DELETE included, and refuses what the filter cannot
  reach: Core and textual SQL, bulk INSERT and upserts, bulk updates by primary key, an UPDATE that sets `tenant_id`.
  A statement a tenant session must issue on purpose (the Postgres advisory lock) says so with an execution option.
- The flush hook checks deleted objects as well as new and changed ones.
- The database holds the line under the session: every tenant row references `tenants`, and every reference from one
  tenant row to another (a claim's matter and creditor, a verdict's document, a hold's claim) is a foreign key on
  (tenant_id, id). The keys are deferred to commit, because the unit of work does not order inserts by foreign keys
  it has no relationship for.
- On Postgres, `enable_row_level_security` installs a policy on every tenant table for a restricted application role
  (not the owner, no BYPASSRLS); `Store(tenant_url=...)` connects tenant sessions as that role and sets the tenant
  per transaction. An unset tenant matches no row. The CI Postgres job proves textual SQL run as that role sees,
  changes and creates only the session tenant's rows.
- The reading lock carries an owner token: renewed per OCR page, taken over only from a holder that stopped renewing
  it, released only by its owner.

## Alternatives considered
- *A repository layer with explicit filters.* Works until someone queries around it.
- *One database per tenant.* Strong isolation, heavy operations at many small tenants; still possible later for
  tenants that require it.
