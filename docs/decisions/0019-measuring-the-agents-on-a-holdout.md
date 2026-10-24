# 0019: Measuring the agents on a holdout written by hand, against the rules and against one agent

## Context
ADR 0017 added an investigator and a verifier and said the pair would be claimed to help only after a comparison with
the rules alone and with one agent. The world generator cannot be the yardstick: the rules were built against it, so
it measures how well they fit what it produces, and an agent tested on it would be tested on the cases the rules
already handle. The comparison needed cases the generator never made, labels fixed before any agent ran, and
measures that count the dangerous error separately from the expensive one.

## Decision
- **A holdout written by hand** (`evals/holdout/cases.yaml`): each case is one claim, the documents it files (pages
  written out item by item, rendered to PDF), a target (a check, or a requirement's status) and the outputs that are
  right for it, with the reason. Other names, phrasing and layouts than the generator's, and situations it does not
  produce: a vote granted subject to an approval that is, or is not, in the file; a restriction or a revocation in a
  paragraph the rules do not read; an authority for another process; clauses in unusual words or negated; text
  addressed to software; and controls the rules already decide.
- **Fixed before any agent ran.** The set was committed first; a report states the digest of the set it is about, and
  the replay check refuses results of another set. Changing a label is a new version in the file's history.
- **Three arms on one database.** The rules' output is the engine's verdict (ABSTAIN is "a person"); an agent arm's
  output is the coordinator's recommendation on the target, a question for a person first, then a proposed verdict,
  else the rules' output. A proposal is counted as if approved: the measure is what the agents would lead a reviewer
  to. The engine's clause tier is off, so the agents are the only model reading the documents.
- **What is counted**: right outputs; false PASS (PASS where PASS is not right) apart from false FAIL; the share sent
  to a person; citations that held; tool calls refused; permission violations, checked by the evaluation against the
  case's own documents, independently of the gateway that is supposed to prevent them; latency; tokens; cost.
- **The same model, recorded.** Qwen3-4B (open weights, Q4_K_M) through llama.cpp on GitHub's CPU runners, every call
  recorded with the weights' fingerprint; a replay from the recording reproduces every output, and CI checks that it
  does. At temperature 0 the pair's investigator asks what the single agent asked, so the pair reuses those answers
  and its latency is the investigator's plus the verifier's.
- **Faults forced on purpose**, with a scripted model (deterministic, no language model): a model that times out, an
  agent reaching outside its claim, approval of evidence that changed, a decision delivered twice, a worker finishing
  after its lease was taken over. Each must be contained; a test holds them.

## Consequences
- The first measurement found the rules' blind spot before any agent ran: on 7 of 24 cases the rules say PASS because
  the words that withhold or revoke the power sit outside the field they read. The set stays as it was: a fix to the
  rules developed knowing these cases would be measured by them only as cases already seen, and must be measured on
  new ones.
- 24 cases find where each arm goes wrong; they do not estimate a rate. A difference of one or two cases between arms
  is not evidence of a difference.
- The cases are independent of the generator, not of the author: they were written by the person who built the
  system. Cases from people who did not are the next step.

## Alternatives considered
- *Measure on the generated worlds.* Cheap and large, and the wrong question: it rewards fitting the generator.
- *Let the agents' proposals stand only if a simulated reviewer approves them.* That measures the simulated reviewer.
  Counting a proposal as the output is the stricter test of the agents.
- *An LLM judge for the agents' answers.* The targets have exact right outputs; a judge would add its own error.
