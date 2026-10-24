"""The tenant's model ledger around a model, for agents that run for minutes.

The engine's ledger (engine._TenantCachedModel) lives inside the transaction of the document it is judging. An agent
calls a model many times between tool calls, so it cannot hold a transaction open (on SQLite that would hold the write
lock for minutes): here the cache and the spend gate are consulted in short transactions, and the model is called
outside any. A repeated question is answered from the ledger for free, by the same weights only, and every call is
counted against the tenant's daily token budget.
"""
from __future__ import annotations

import json

from .. import budget
from ..engine import Engine
from ..modeltier import ModelClient, Reply, ask, call_key, fingerprint_of
from ..store import ModelCall, UsageRow


class LedgeredModel:
    def __init__(self, engine: Engine, tenant_id: int, inner: ModelClient):
        self.engine, self.tenant_id, self.inner = engine, tenant_id, inner
        self.model_id = inner.model_id
        self.fingerprint = fingerprint_of(inner)

    def chat(self, messages: list[dict], max_tokens: int = 200, schema: dict | None = None) -> Reply:
        key = call_key(self.model_id, messages, max_tokens, schema)
        today = self.engine.clock().date()
        with self.engine.store.tenant(self.tenant_id) as s:
            row = s.get(ModelCall, {"key": key, "tenant_id": self.tenant_id})
            if row is not None and row.fingerprint == self.fingerprint:
                s.add(UsageRow(day=today, provider="model", units=row.prompt_tokens + row.completion_tokens,
                               cached=True))
                return Reply(row.response, row.prompt_tokens, row.completion_tokens, 0.0, cached=True)
            budget.check(s, self.tenant_id, "model", len(json.dumps(messages)) / 4 + max_tokens, today)
        reply = ask(self.inner, messages, max_tokens, schema)
        with self.engine.store.tenant(self.tenant_id) as s:
            row = s.get(ModelCall, {"key": key, "tenant_id": self.tenant_id})
            if row is not None and row.fingerprint != self.fingerprint:
                s.delete(row)                       # other weights answered before: replaced
                s.flush()
                row = None
            if row is None:                         # another worker may have written the same answer meanwhile
                s.add(ModelCall(key=key, model=self.model_id, fingerprint=self.fingerprint, response=reply.text,
                                prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
                                seconds=reply.seconds))
            s.add(UsageRow(day=today, provider="model", units=reply.prompt_tokens + reply.completion_tokens,
                           cached=reply.cached))
        return reply
