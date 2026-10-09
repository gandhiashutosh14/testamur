# 0011: Findings become work through a transactional outbox

## Context
A status nobody acts on is a dashboard. When a certificate lapses, someone must chase the supplier, the buyer's
purchasing system may need to stop new orders, and the buyer's own systems want to know. Each of those is a side
effect, and side effects are where pipelines go wrong: a crash after the status changed but before the reminder was
written loses the reminder; a retry after the reminder was written sends it twice; a webhook that timed out may or
may not have been received.

## Decision
- **Outbox in the same transaction.** When a requirement's status or its failing checks change, the engine writes an
  event row in the same transaction as the change (`OutboxEvent`, unique key per requirement and version). Either
  both commit or neither does.
- **Dispatch per event, in one transaction.** The dispatcher handles events oldest first. For each, it writes the
  resulting rows (a task, a reminder, a hold, webhook messages) and marks the event dispatched, all in one
  transaction. A crash or an error anywhere rolls the whole event back, and it is handled again from scratch.
- **The database keeps actions single.** At most one open task per requirement and one open hold per engagement
  (partial unique indexes); reminders and webhook messages carry unique keys. Doing an event twice cannot double
  anything, whatever the code does.
- **Outside effects after the transaction, at least once.** Webhook messages are sent by a separate step, signed
  (Standard Webhooks: HMAC-SHA256 over id, timestamp and body), retried with doubling backoff, and declared dead
  after eight attempts, for a person to see. The message id is stable across retries, so the receiver processes
  each message once even when its acknowledgement was lost; stale timestamps are refused, so an old message cannot
  be replayed.
- **Actions undo themselves.** A task closes and a hold is released when the requirement is met again, by the same
  dispatcher, from the same events.

## Consequences
- The simulation (`testamur actionsim`) kills the dispatcher in the middle of an event and runs a receiver that is
  down a fifth of the time and loses a tenth of its acknowledgements; the invariants hold: one event per status
  change, at most one open task per requirement, at most one open hold per engagement, each message processed once.
- Events are dispatched in order per tenant; a slow event delays the ones after it. A broker can replace the polling
  dispatcher later behind the same two calls (dispatch, deliver).
- Reminders are written, not sent: the e-mail gateway is outside this repository.

## Alternatives considered
- *Act inside the status computation.* Couples a pure computation to the network, and a failed call would roll back
  a correct status.
- *Exactly-once delivery.* Not available over HTTP; at-least-once with an id the receiver deduplicates on is the
  standard answer.
