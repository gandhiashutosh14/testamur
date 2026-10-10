# From finding to action: a simulation

Commit `6b2b49d`, command `testamur actionsim --world data/world_full11 --db data/actionsim.db --days 60 --report reports/action-sim-full-seed11.md --cassette reports/cassettes/full-seed11.jsonl`. World seed 11 (full), 60 simulated days. Creditors with an open chase answer with a renewed authority with daily probability 15%; the firm's webhook receiver is down 20% of the time and loses 10% of its acknowledgements.

## What the engine did

- Status changes reported: 469 events, every one dispatched (0 left over); one event per status change: yes.
- Tasks: chase_creditor:done 15, chase_creditor:open 1, follow_up:open 93. A chase closed by itself a median 4.0 days after it opened, when the renewed authority arrived.
- Creditor reminders: 18 written; 0 more were held back by the one-a-week cooldown.
- Holds (votes not counted, nothing paid): 46 placed, 2 released by themselves when the evidence was put right.
- Renewed authorities uploaded by creditors: 15.

## Delivery to the firm's systems

509 webhook messages: delivered 509, in 689 attempts.

| Tenant | Processed | Distinct | Duplicates ignored | Refused while down | Bad signature |
|---|---|---|---|---|---|
| 1 | 163 | 163 | 20 | 55 | 0 |
| 2 | 169 | 169 | 5 | 42 | 0 |
| 3 | 177 | 177 | 19 | 39 | 0 |

## Invariants

- At most one open task per requirement: True (max 1).
- At most one open hold per claim: True.
- Each webhook message processed once by the receiver: True.
- The dispatcher, killed in the middle of an event on the first dispatch, left no partial work: the event was handled again from scratch on restart.

## Over time

| Day | Date | Open chases | Open follow-ups | Holds | Expired requirements |
|---|---|---|---|---|---|
| 1 | 2026-10-02 | 10 | 93 | 46 | 3 |
| 5 | 2026-10-06 | 5 | 93 | 46 | 2 |
| 10 | 2026-10-11 | 3 | 93 | 45 | 3 |
| 15 | 2026-10-16 | 1 | 93 | 45 | 1 |
| 20 | 2026-10-21 | 1 | 93 | 45 | 1 |
| 25 | 2026-10-26 | 1 | 93 | 45 | 1 |
| 30 | 2026-10-31 | 1 | 93 | 45 | 1 |
| 35 | 2026-11-05 | 0 | 93 | 45 | 0 |
| 40 | 2026-11-10 | 1 | 93 | 45 | 0 |
| 45 | 2026-11-15 | 2 | 93 | 45 | 0 |
| 50 | 2026-11-20 | 2 | 93 | 45 | 0 |
| 55 | 2026-11-25 | 1 | 93 | 45 | 0 |
| 60 | 2026-11-30 | 1 | 93 | 45 | 0 |

## What this does not show

- Creditors are simulated: they answer at a fixed rate with a clean authority. Real creditors send the wrong document, an unsigned draft, or nothing.
- The e-mail gateway and the firm's systems are simulated; the signing, retries and duplicate handling are the real code.
