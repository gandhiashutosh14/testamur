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

## Alternatives considered
- *A repository layer with explicit filters.* Works until someone queries around it.
- *One database per tenant.* Strong isolation, heavy operations at many small tenants; still possible later for
  tenants that require it.
