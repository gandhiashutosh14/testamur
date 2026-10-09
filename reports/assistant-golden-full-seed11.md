# Assistant golden set

Commit `557af47`, command `testamur golden --world data/world_full11 --db data/harness_full11.db --report reports/assistant-golden-full-seed11.md`. World seed 11 (full). 229 questions built from the ground truth; 229 answered right.

| Question type | Questions | Right | Unsettled by the engine | Cited the current document | Wrong value | Wrong version | Wrong entity | Cited outside the tenant |
|---|---|---|---|---|---|---|---|---|
| Ambiguous names | 22 | 22 | - | - | - | - | - | 0 |
| Another tenant's supplier | 15 | 15 | - | - | - | - | - | 0 |
| Expiring certificates | 6 | 6 | - | - | - | - | - | 0 |
| Field lookups | 144 | 144 | 14 | 130 | 0 | 0 | 0 | 0 |
| Field lookups on hostile documents | 12 | 12 | 0 | 12 | 0 | 0 | 0 | 0 |
| Near-twin names | 21 | 21 | - | - | - | - | - | 0 |
| Unanswerable | 9 | 9 | - | - | - | - | - | 0 |

Version trap: 20 questions about suppliers with an older, superseded certificate on file; 20 answered from the current one.

## What this does not show

- The questions are templated from the generator's truth; real staff ask in many more ways. The intent routing is rules, and a question it does not recognise falls through to retrieval.
- Answers come from the engine's own readings, so a field the engine misread would be answered wrongly with a correct citation; the harness measures the readings themselves.
