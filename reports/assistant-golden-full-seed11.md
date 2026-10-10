# Assistant golden set

Commit `6b2b49d`, command `testamur golden --world data/world_full11 --db data/harness_full11.db --report reports/assistant-golden-full-seed11.md`. World seed 11 (full). 269 questions built from the ground truth; 269 answered right.

| Question type | Questions | Right | Unsettled by the engine | Cited the current document | Wrong value | Wrong version | Wrong entity | Cited outside the tenant |
|---|---|---|---|---|---|---|---|---|
| Ambiguous names | 18 | 18 | - | - | - | - | - | 0 |
| Another tenant's creditor | 15 | 15 | - | - | - | - | - | 0 |
| Lapsing authorities | 6 | 6 | - | - | - | - | - | 0 |
| Field lookups | 157 | 157 | 9 | 148 | 0 | 0 | 0 | 0 |
| Field lookups on hostile documents | 6 | 6 | 0 | 6 | 0 | 0 | 0 | 0 |
| Near-twin names | 58 | 58 | - | - | - | - | - | 0 |
| Unanswerable | 9 | 9 | - | - | - | - | - | 0 |

Version trap: 7 questions about creditors with an older, superseded authority on file; 7 answered from the current one.

## What this does not show

- The questions are templated from the generator's truth; real staff ask in many more ways. The intent routing is rules, and a question it does not recognise falls through to retrieval.
- Answers come from the engine's own readings, so a field the engine misread would be answered wrongly with a correct citation; the harness measures the readings themselves.
