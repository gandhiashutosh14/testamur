# From finding to action: a simulation

Commit `f16af51`, command `testamur actionsim --world data/world_full11 --db data/actionsim.db --days 60 --report reports/action-sim-full-seed11.md --cassette reports/cassettes/full-seed11.jsonl`. World seed 11 (full), 60 simulated days. Creditors with an open chase answer with a renewed authority with daily probability 15%; the firm's webhook receiver is down 20% of the time and loses 10% of its acknowledgements.

## What the engine did

- Status changes reported: 993 events, every one dispatched (0 left over); one event per status change: yes.
- Tasks: chase_creditor:done 22, follow_up:open 109. A chase closed by itself a median 4.0 days after it opened, when the renewed authority arrived.
- Creditor reminders: 23 written; 0 more were held back by the one-a-week cooldown.
- Holds (votes not counted, nothing paid): 115 placed, 11 released by themselves when the evidence was put right.
- Renewed authorities uploaded by creditors: 22.

## Delivery to the firm's systems

829 webhook messages: delivered 829, in 1129 attempts.

| Tenant | Processed | Distinct | Duplicates ignored | Refused while down | Bad signature |
|---|---|---|---|---|---|
| 1 | 265 | 265 | 31 | 80 | 0 |
| 2 | 281 | 281 | 14 | 68 | 0 |
| 3 | 283 | 283 | 36 | 71 | 0 |

## Invariants

- At most one open task per requirement: True (max 1).
- At most one open hold per claim and action: True (max 1).
- Each webhook message processed once by the receiver: True.
- The dispatcher, killed in the middle of an event on the first dispatch, left no partial work: the event was handled again from scratch on restart.

## Over time

| Day | Date | Open chases | Open follow-ups | Holds | Expired requirements |
|---|---|---|---|---|---|
| 1 | 2026-10-02 | 15 | 109 | 107 | 3 |
| 5 | 2026-10-06 | 9 | 109 | 104 | 2 |
| 10 | 2026-10-11 | 5 | 109 | 102 | 2 |
| 15 | 2026-10-16 | 2 | 109 | 102 | 1 |
| 20 | 2026-10-21 | 0 | 109 | 101 | 0 |
| 25 | 2026-10-26 | 0 | 109 | 101 | 0 |
| 30 | 2026-10-31 | 0 | 109 | 101 | 0 |
| 35 | 2026-11-05 | 0 | 109 | 102 | 0 |
| 40 | 2026-11-10 | 1 | 109 | 103 | 0 |
| 45 | 2026-11-15 | 1 | 109 | 103 | 0 |
| 50 | 2026-11-20 | 2 | 109 | 103 | 0 |
| 55 | 2026-11-25 | 1 | 109 | 103 | 0 |
| 60 | 2026-11-30 | 0 | 109 | 104 | 0 |

## What this does not show

- Creditors are simulated: they answer at a fixed rate with a clean authority. Real creditors send the wrong document, an unsigned draft, or nothing.
- The e-mail gateway and the firm's systems are simulated; the signing, retries and duplicate handling are the real code.
