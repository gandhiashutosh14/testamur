# Load test: full world, seed 11

Commit `6b2b49d`, command `testamur loadtest --world data/world_full11 --out data/load_full11 --workers 1,2,4 --budget-pages 40 --report reports/loadtest-full-seed11.md`, 4 logical CPUs on a GitHub-hosted runner. Every upload is enqueued at once; worker processes drain the queue; the clause model is off (OCR is the cost being measured).

## Scaling

| Workers | Uploads | Wall time | Uploads/min | OCR pages | OCR pages/min | Job latency p50 / p95 | Dead | Statuses identical to 1 worker |
|---|---|---|---|---|---|---|---|---|
| 1 | 412 | 294.7 s | 83.9 | 175 | 35.6 | 169.0 s / 283.8 s | 0 | yes |
| 2 | 412 | 240.0 s | 103.0 | 175 | 43.7 | 137.9 s / 228.4 s | 0 | yes |
| 4 | 412 | 224.9 s | 109.9 | 175 | 46.7 | 125.3 s / 215.0 s | 0 | yes |

## Spend gate

Tenant 1 capped at 40 OCR pages a day. After the first day 16 jobs were waiting (deferred, not failed); the clock was advanced a day at a time and the queue drained after 1 extra days. The most pages paid on any day: 40 (the bound is the larger of the cap and the largest single document, 2 pages: held). Jobs done: 412 of 412; dead: 0. Final statuses identical to the run without a cap: yes.

## Crash recovery

Worker 0 was killed abruptly after leasing its third job (exit codes [17, 0]). After its lease expired, a second pass finished the queue: 412 of 412 jobs done, 1 job(s) taken over after the crash, 0 dead. Final statuses identical to the uninterrupted run: yes.

## What this does not show

- One machine, SQLite: the database serialises the short bookkeeping transactions; OCR, the expensive part, runs outside them. A Postgres deployment would lock per tenant instead.
- Timings are this machine's, not a benchmark.
