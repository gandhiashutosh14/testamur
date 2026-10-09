# Load test: full world, seed 11

Commit `b52caf3`, command `testamur loadtest --world data/world_full11 --out data/load_full11 --report reports/loadtest-full-seed11.md --workers 1,2,4 --budget-pages 40`, 16 logical CPUs on the author's laptop. Every upload is enqueued at once; worker processes drain the queue; the clause model is off (OCR is the cost being measured).

## Scaling

| Workers | Uploads | Wall time | Uploads/min | OCR pages | OCR pages/min | Job latency p50 / p95 | Dead | Statuses identical to 1 worker |
|---|---|---|---|---|---|---|---|---|
| 1 | 378 | 223.4 s | 101.5 | 146 | 39.2 | 117.4 s / 212.0 s | 0 | yes |
| 2 | 378 | 163.2 s | 139.0 | 146 | 53.7 | 84.3 s / 156.1 s | 0 | yes |
| 4 | 378 | 111.5 s | 203.5 | 146 | 78.6 | 58.4 s / 107.0 s | 0 | yes |

## Spend gate

Tenant 1 capped at 40 OCR pages a day. After the first day 12 jobs were waiting (deferred, not failed); the clock was advanced a day at a time and the queue drained after 1 extra days. The most pages paid on any day: 40 (the bound is the larger of the cap and the largest single document, 2 pages: held). Jobs done: 378 of 378; dead: 0. Final statuses identical to the run without a cap: yes.

## Crash recovery

Worker 0 was killed abruptly after leasing its third job (exit codes [17, 0]). After its lease expired, a second pass finished the queue: 378 of 378 jobs done, 1 job(s) taken over after the crash, 0 dead. Final statuses identical to the uninterrupted run: yes.

## What this does not show

- One laptop, SQLite: the database serialises the short bookkeeping transactions; OCR, the expensive part, runs outside them. A Postgres deployment would lock per tenant instead.
- Timings are this machine's, not a benchmark.
