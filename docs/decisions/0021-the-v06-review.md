# 0021: What an independent review of v0.6 found, and what changed

## Context
An independent review of v0.6 (main at `e469b45`, release v0.6.0) read the code, recomputed the reports' aggregates
from their rows, and wrote seven tests of invariants the project claims. All seven failed there. They found five gaps:

- an agent's citation of eight spaces passed (the contract counts characters, and the coordinator's short-quote
  fallback found the empty string, after trimming, on every page);
- a verifier that said "supported" with no citation of its own counted as the second agent agreeing;
- an approval applied just before a crash was reported as "rejected" when the run recovered, because applying it
  again was refused as "already applied";
- the replay checks compared verdicts and judge scores only, not retrieval, answers, citations or tokens, and a task
  held twice collapsed into one;
- the TAT-QA scorer credited any number anywhere in an answer, in either of two scales: "The result is 20 dollars;
  5 was the starting value" and "5 dollars" were both right against five million dollars.

The review also named claims stronger than their evidence: "every verdict cites the line on the page" (a missing
document has no line), "retrieval was not the problem" (a hit is a word-overlap heuristic), a judge "no better than a
coin flip" (faithfulness measures support in the retrieved passages, not correctness), the service's "ask this
document" retrieving by BM25 while the evaluation's headline is hybrid, and a holdout that became a development set
once run 2 followed changes made after run 1.

## Decision
- **A citation is text from the page.** A quote without a letter or a digit (blank, whitespace of any kind) never
  holds: one check, `quote_holds`, for the coordinator and the assistant, and the agents' contract refuses such a
  quote too, with a validator that leaves the JSON schema the model is given unchanged, so the recordings still
  replay.
- **Agreement needs the verifier's own evidence.** The pair counts as agreeing only where the verifier's review keeps
  a citation that holds; an uncited endorsement leaves the finding unresolved, and the claim goes to a person.
- **Recovery reports what happened.** An apply step that runs again finds what each decision already came to: the
  outcome its proposal recorded, if the same person decided it the same way. Another reviewer's decision is reported
  as theirs ("already applied by ..."), never taken as this run's.
- **A replay is held to everything it reproduces.** Every field of a task (retrieval ranks, the answer, its citations
  and their verification, the verdicts, tokens, the judge's calls, scores and skips) or of a case (each arm's output
  and run: status, stops, steps, tool calls, citations, recommendations, tokens); not timings, nor how many calls a
  run spared the model, which a replay does not reproduce; answer relevancy within 0.001, the one tolerance, because
  it compares embeddings computed where it replays. A task or case held twice or missing is a difference; shards must
  agree on their embedder and k; results must be the committed sample's; a call the recording does not have fails the
  run instead of being scored as an error.
- **A number is the quantity an answer states.** TAT-QA's computed answers must state one quantity (years aside): an
  answer that states two, a result and a figure it worked from, or a value it denies and one it asserts, is not taken
  to assert either. It is compared in the gold's unit, with its sign, within 0.5%: a percentage as one or bare, never
  as money or with a scale word; an amount never as a percentage, at its scale word's scale ("5 dollars" is a scale
  of one), else at the table's or written out in full. A gold span that is a number is compared as one, so the
  brackets of "(43)" are a sign. This is still adapted scoring, not TAT-QA's official evaluator.
- **The same recordings, scored again.** No model ran: the v0.6.0 recordings were replayed on GitHub's runners under
  the fixed code (`measure.yml`, `rescore`), and the reports written with the recorded runs' timings. The v0.6.0
  reports stay beside the new ones as `reports/*-v0.6.0.*`; the sample, the cases and their labels did not change.
- **Words no stronger than the evidence.** The README, the reports and the tour now say: a verdict carries the line of
  the page where there is one; a retrieval hit is a heuristic; faithfulness is a weak stand-in for correctness here,
  not proof that the judge or RAGAS fails in general; "ask this document" retrieves by BM25, so the report's BM25
  column is the one that applies to it; the holdout is a development set now.

## What the rescoring moved
**The evidence QA** ([report](../../reports/evidence-eval.md); as scored at v0.6.0:
[report](../../reports/evidence-eval-v0.6.0.md)): two tasks moved, both TAT-QA and one each way, so every total
stands: 86 of 160 outcomes right as answered and 80 under the evidence rule, 58 wrong answers and 51 under the rule,
49 of the 58 with a retrieval hit.
- `tatqa:b9b23aab137d`, right to wrong: the gold is two losses, "(43), (118)" in thousands, and the answer "43 and
  118" drops their sign. Token F1 had matched the digits.
- `tatqa:5dbc3dcd6f59`, wrong to right: the gold is two amounts, "30,303, 303,793", and the answer states exactly
  those two, each after a label of a dozen words, which token F1 had marked down.

No computed answer moved: the recorded answers that state more than one quantity were wrong already. The rules would
have moved answers like the review's examples.

The two moved labels shifted the judge's AUROCs (faithfulness from 0.476 to 0.497), which shows how loose they are on
115 answers. The report now gives each a 95% bootstrap interval. Faithfulness's (0.411 to 0.586) holds 0.5: as a
stand-in for correctness it cannot be told from chance on this sample. Context precision (0.608, 0.52 to 0.70) and
factual correctness (0.62, 0.514 to 0.721) carry a little signal.

**The holdout** ([report](../../reports/holdout-eval.md)): no output of any arm changed, so the headline numbers
stand. In two cases (`interest-without-the-word-rate`, `no-interest-term-at-all`) the verifier had endorsed a finding
without a citation of its own that held; the pair's recommendation there is now "unresolved" instead of agreed by
both. Both were questions for a person either way.

## Consequences
- The review's seven tests are in `tests/test_v06_independent_review.py`, with cases beside each (whitespace of every
  kind, a verifier citing what is not on the page, every crash window of an approval, each compared field, units,
  signs, negations, accounting brackets, numeric spans), and ten more hand-picked mutants that the tests catch.
- A replay check that passes now means every recorded result came back, not only the verdicts.
- Scores can move when a scorer is fixed; when they do, the old report stays, and this record lists every task that
  moved and why.

## Not done
- One retrieval path for the service and the evaluation, evaluated on the request path.
- A fresh, sealed evaluation with evidence sufficiency and correctness labelled independently; the judge against
  blinded human labels, with intervals; a stronger judge measured the same way.
- Branch protection requiring `gate` and an independent review: repository settings, and on a one-maintainer
  repository the `risk` check's hold is released by the maintainer.
