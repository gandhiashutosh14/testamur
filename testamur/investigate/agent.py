"""A bounded tool-using agent: a loop of model turns, each one JSON object, each either a tool call or the final answer.

Bounded in every direction: a number of steps, a number of tokens, a wall-clock deadline, a number of invalid answers,
and a cancellation check between steps. The answer must validate against the agent's output contract (Pydantic); an
invalid answer is sent back with the validation error, once or twice, and then the agent stops. Each turn carries a
short note saying what it is doing, recorded in the trace in place of any hidden reasoning, which is never asked for
or kept. The model sees only what the tool gateway returns; the gateway decides what it may see.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..telemetry import span
from .tools import ToolGateway

MAX_INVALID = 2
STEP_TOKENS = 1200      # the longest answer one step may give: a finished packet of findings with quotes


@dataclass
class Budget:
    steps: int = 8
    # prompt + completion tokens, summed over the agent's calls; each call's prompt is the whole conversation so far,
    # so eight steps of reading pages come to about 40,000
    tokens: int = 60_000
    seconds: float = 900.0


@dataclass
class AgentResult:
    output: BaseModel | None
    stop: str                          # finished | step budget | token budget | time budget | invalid output | ...
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    cached_calls: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def step_schema(tools: list[str], output: type[BaseModel], finish_only: bool = False) -> dict:
    """What one turn may say: a tool call or the final answer, with a short note. Given to the model as a JSON schema
    (llama.cpp constrains decoding to it) and checked again here whatever the model produced. On the last step only
    the answer is allowed: an agent that would go on reading answers with what it has instead of answering nothing."""
    if finish_only:
        return {"type": "object", "properties": {
            "note": {"type": "string", "maxLength": 160},
            "action": {"type": "string", "enum": ["finish"]},
            "result": _inline(output.model_json_schema())},
            "required": ["note", "action", "result"]}
    return {"type": "object", "properties": {
        "note": {"type": "string", "maxLength": 160},
        "action": {"type": "string", "enum": ["call_tool", "finish"]},
        "tool": {"type": "string", "enum": tools},
        "args": {"type": "object"},
        "result": _inline(output.model_json_schema())},
        "required": ["note", "action"]}


def run(model, gateway: ToolGateway, system: str, task: str, output: type[BaseModel], budget: Budget,
        cancelled=lambda: False) -> AgentResult:
    """Run the agent to its answer or to the end of its budget. `model.chat(messages, max_tokens, schema=...)`."""
    with span("agent", tenant_id=gateway.tenant_id, claim_id=gateway.claim_id, role=gateway.role) as note:
        result = _run(model, gateway, system, task, output, budget, cancelled)
        note(outcome=result.stop, steps=result.steps, prompt_tokens=result.prompt_tokens,
             completion_tokens=result.completion_tokens, cached_calls=result.cached_calls)
        return result


def _run(model, gateway: ToolGateway, system: str, task: str, output: type[BaseModel], budget: Budget,
         cancelled) -> AgentResult:
    tools = [t["name"] for t in gateway.describe()]
    schema, answer_now = step_schema(tools, output), step_schema(tools, output, finish_only=True)
    catalogue = "\n".join(f"- {t['name']}({', '.join(f'{k}: {v}' for k, v in t['args'].items())}): "
                          f"{t['description']}" for t in gateway.describe())
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"{task}\n\nYour tools:\n{catalogue}\n\n{_FORMAT}"}]
    result = AgentResult(None, "step budget")
    t0, invalid = time.perf_counter(), 0
    for step in range(1, budget.steps + 1):
        if cancelled():
            result.stop = "cancelled"
            break
        if result.tokens >= budget.tokens:
            result.stop = "token budget"
            break
        if time.perf_counter() - t0 >= budget.seconds:
            result.stop = "time budget"
            break
        gateway.step = step
        last = step == budget.steps
        if last:
            messages.append({"role": "user", "content": LAST_STEP})
        try:
            reply = model.chat(messages, max_tokens=STEP_TOKENS, schema=answer_now if last else schema)
        except Exception as exc:  # noqa: BLE001 - a provider failure ends the agent; the coordinator decides
            result.stop = f"model error: {type(exc).__name__}"
            break
        result.steps = step
        result.prompt_tokens += reply.prompt_tokens
        result.completion_tokens += reply.completion_tokens
        result.cached_calls += bool(reply.cached)
        turn = _parse(reply.text)
        messages.append({"role": "assistant", "content": reply.text})
        if turn is None or turn.get("action") not in ("call_tool", "finish"):
            invalid += 1
            if invalid > MAX_INVALID:
                result.stop = "invalid output"
                break
            messages.append({"role": "user", "content": "That was not one JSON object with an action. " + _FORMAT})
            continue
        result.notes.append(str(turn.get("note", ""))[:160])
        if turn["action"] == "call_tool":
            if last:                       # a client that ignored the schema: the last step was for the answer
                break
            answer = gateway.call(str(turn.get("tool", "")), turn.get("args") if isinstance(turn.get("args"), dict)
                                  else {})
            left = budget.steps - step
            messages.append({"role": "user", "content": f"Result of {turn.get('tool')} (step {step} of "
                                                         f"{budget.steps}: {left} left, the last of them for your "
                                                         f"answer): {json.dumps(answer, default=str)[:2400]}"})
            continue
        try:
            result.output = output.model_validate(turn.get("result") or {})
            result.stop = "finished"
            break
        except ValidationError as exc:
            invalid += 1
            if invalid > MAX_INVALID:
                result.stop = "invalid output"
                break
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
            messages.append({"role": "user", "content": f"The result does not fit the contract ({problems}). "
                                                         "Fix it and finish again."})
    result.seconds = round(time.perf_counter() - t0, 2)
    return result


LAST_STEP = ("This is your last step: finish now with the findings you have, and put what you could not settle in "
             "open questions. Cite only what you read.")

_FORMAT = ("Reply with exactly one JSON object per turn. To use a tool: "
           '{"note": "what you are doing, in a few words", "action": "call_tool", "tool": "<name>", '
           '"args": {...}}. When you are done: {"note": "...", "action": "finish", "result": {...}}, where result '
           "follows the contract you were given. Cite only what you read: a citation is a document id, a page and "
           "words copied exactly from that page.")


def _parse(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = (text or "").find("{"), (text or "").rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _inline(schema: dict) -> dict:
    """A JSON schema with its local $refs replaced by the definitions they name (grammar converters want it flat)."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(schema)
