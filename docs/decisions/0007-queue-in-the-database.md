# 0007: A job queue in the database, with leases, and the slow work outside the lock

## Context
A large customer can deliver thousands of documents in a day, unevenly. Processing one document means OCR (seconds
of CPU, paid), a short burst of bookkeeping (matching, checks, statuses) and sometimes model calls. Work must
survive a crashed worker, never be done twice in a way that costs twice, never be lost when a tenant hits its spend
cap, and never leave a status computed from a snapshot another worker was changing.

## Decision
- **Queue in the same database.** A `jobs` table, claimed with one `UPDATE ... RETURNING` (Postgres adds `FOR UPDATE
  SKIP LOCKED`). No second piece of infrastructure to run, back up and monitor; a broker can replace it later behind
  the same four calls (enqueue, lease, complete, fail/defer).
- **Leases, not deletes.** A claimed job carries a lease that expires. A worker that dies mid-job leaves the lease to
  run out and another worker takes the job. The lease must be longer than the slowest job.
- **Idempotent work.** Documents are content-addressed, extractions and model calls are ledgered by content and
  prompt, verdicts are upserted: running a job twice produces the same state and pays nothing the second time.
- **Retries with backoff, then a dead-letter state** with the last error, so a poison document stops being retried
  and a person sees it.
- **Spend gates defer, with a reservation.** A tenant over its daily OCR or model budget gets its job deferred to
  the reset time, without counting an attempt. The check reserves what it allows in the same transaction, so two
  workers cannot both spend the last of a budget (the first version could; the load test caught it).
- **OCR outside the lock.** On SQLite every transaction begins IMMEDIATE, so status bookkeeping is serialised and
  correct. OCR, the slow part, runs before and outside any transaction (`Engine.prefetch`), so workers overlap where
  it matters. On Postgres a tenant session takes a transaction-scoped advisory lock instead: tenants run in parallel.

## Revised in v0.4: fencing
An external review showed the lease was a timer, not a fence. Completion, failure and deferral updated the job by id
alone, so a worker that stalled past its lease and came back could mark done a job another worker now held, and the
killed-worker experiment never tested a worker coming back. Now every lease carries a fresh token; completing,
failing, deferring and renewing need it, in the same UPDATE, and a worker whose token no longer matches gets
`LeaseLost` and records nothing. Long work renews its lease with every OCR page (a heartbeat). A job whose lease ran
out on every attempt is dead-lettered instead of being handed to the next worker. A reservation of OCR budget left by
a worker that died is reconciled when another worker takes its stale reading lock over: it stays counted (it may
have been paid for) and is marked abandoned instead of pending.

## Consequences
- The load test shows the final statuses are identical whether one worker or several processed the burst, whether a
  spend cap stretched it over several days, and whether a worker was killed mid-job.
- SQLite's single writer caps bookkeeping throughput on one machine; the design keeps that part short, and Postgres
  removes the cap per tenant.

## Alternatives considered
- *An in-process queue.* Loses in-flight work on a restart; nothing to measure a crash against.
- *A message broker from day one.* More moving parts than one engineer should run before the volume needs it.
