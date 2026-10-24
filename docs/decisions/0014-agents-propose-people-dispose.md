# 0014: A tool server for agents: one tenant per key, a catalogue per tenant, and proposals instead of writes

## Context
Customers will point their own agents at the platform: to ask which creditors are blocked, to work the review queue,
to draft the chase for a lapsing authority. An agent calls tools far more often than a person clicks, follows
instructions it reads, and is not the person accountable for admitting a claim. A tool surface for agents has to
assume all three.

## Decision
- **The Model Context Protocol** (official Python SDK) over stdio; the same tools would serve over HTTP.
- **One tenant per key.** A key is created for a named agent of one tenant and stored only as a hash. The key decides
  the tenant before anything else happens; every tool then runs in that tenant's session, so tenant isolation is
  the session's (ADR 0003), not each tool's.
- **A catalogue per tenant.** The tenant grants and revokes tools by name. The server lists only granted tools, so
  an agent does not even learn of the others, and checks the grant on every call, so a revocation takes effect
  immediately, without a restart.
- **Reads paginate with a fixed number of queries.** Lists take a cursor and a page size (at most 100) and run the
  same handful of queries whatever the page size, measured by a test: an agent loop that read row by row would turn
  one call into hundreds of queries.
- **Agents propose; people dispose.** There is no write tool. An agent can propose a check verdict, a field reading or
  a document decision; the proposal is checked for sense and recorded, and nothing changes. A person confirms or
  declines it (`testamur proposals`); a confirmed proposal is applied through the same engine call a person would
  use, under the person's name with the agent's beside it, and audited. A proposal is applied at most once and
  expires after a week.
- **Tool annotations say so**: reads are marked read-only, proposals as non-destructive.

## Revised in v0.4: the key on every call, grants per agent
An external review found the server authenticated its key once, when it was built: revoking the key stopped new
servers but not one already running. The key is now authenticated again on every call, beside the grant check, so a
revoked key stops working at once (`testamur keys revoke`). Grants can be made to every agent of a tenant or to one
agent by name, so agents with different jobs (an investigator, a verifier) hold different tools.

## Consequences
- An agent with every tool granted still cannot change a status by itself, cannot see another tenant, and cannot
  call a tool its tenant revoked a second ago.
- The confirmation step is a person's work: the proposal queue is part of the review queue's job, and its size is
  something to watch.

## Alternatives considered
- *Write tools behind an approval prompt in the client.* The approval would live in software the platform does not
  control; the platform must hold the rule itself.
- *One server for all tenants with a tenant parameter on each tool.* One forgotten parameter check is a leak.
