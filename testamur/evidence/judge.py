"""RAGAS, with an open-weight judge on the CPU instead of a paid API.

RAGAS 0.4's metrics (ragas.metrics.collections) ask their LLM for a Pydantic model: `agenerate(prompt, Model)`. The
adapter here answers through TESTAMUR's model clients, with decoding constrained to the model's JSON schema (llama.cpp
compiles it to a grammar, so the answer always parses), every call recorded and replayed by a Cassette. The judge is
of another model family than the one that answered (Phi-4-mini judging Qwen3-4B), so no model grades its own words.

Five metrics: faithfulness (are the answer's statements supported by the retrieved passages), answer relevancy (does
it answer the question; embeddings), context precision and context recall (did retrieval bring the passages the
reference needs), and factual correctness (do the answer's claims match the reference's). A metric that needs a
reference is not scored where a task has none, and a metric that needs an answer is not scored on an abstention.
"""
from __future__ import annotations

import time
import typing as t

from ragas.embeddings.base import BaseRagasEmbedding
from ragas.llms.base import InstructorBaseRagasLLM

from ..modeltier import ask

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall", "factual_correctness")


class RagasLLM(InstructorBaseRagasLLM):
    def __init__(self, model, max_tokens: int = 900):
        self.model, self.max_tokens = model, max_tokens
        self.calls = self.failures = self.prompt_tokens = self.completion_tokens = 0
        self.seconds = 0.0

    def generate(self, prompt: str, response_model: type[t.Any]) -> t.Any:
        t0 = time.perf_counter()
        self.calls += 1
        try:
            reply = ask(self.model, [{"role": "user", "content": prompt}], self.max_tokens,
                        response_model.model_json_schema())
            self.prompt_tokens += reply.prompt_tokens
            self.completion_tokens += reply.completion_tokens
            return response_model.model_validate_json(reply.text)
        except Exception:
            self.failures += 1
            raise
        finally:
            self.seconds += time.perf_counter() - t0

    async def agenerate(self, prompt: str, response_model: type[t.Any]) -> t.Any:
        return self.generate(prompt, response_model)      # one call at a time: a CPU model serves one


class RagasEmbeddings(BaseRagasEmbedding):
    def __init__(self, embedder):
        super().__init__()
        self.embedder = embedder

    def embed_text(self, text: str, **kwargs: t.Any) -> list[float]:
        return self.embedder.embed_query(text).tolist()

    async def aembed_text(self, text: str, **kwargs: t.Any) -> list[float]:
        return self.embed_text(text)


class Judge:
    def __init__(self, model, embedder):
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecisionWithReference,
            ContextRecall,
            FactualCorrectness,
            Faithfulness,
        )
        self.llm = RagasLLM(model)
        emb = RagasEmbeddings(embedder)
        self.model_id = getattr(model, "model_id", "?")
        self.fingerprint = getattr(model, "fingerprint", None)
        self.metrics = {"faithfulness": Faithfulness(llm=self.llm),
                        "answer_relevancy": AnswerRelevancy(llm=self.llm, embeddings=emb),
                        "context_precision": ContextPrecisionWithReference(llm=self.llm),
                        "context_recall": ContextRecall(llm=self.llm),
                        "factual_correctness": FactualCorrectness(llm=self.llm)}

    def score(self, question: str, response: str | None, contexts: list[str], reference: str) -> dict:
        """Each metric that applies, as a float, or None with the reason it was not scored."""
        args = {
            "faithfulness": dict(user_input=question, response=response, retrieved_contexts=contexts)
            if response else None,
            "answer_relevancy": dict(user_input=question, response=response) if response else None,
            "context_precision": dict(user_input=question, reference=reference, retrieved_contexts=contexts)
            if reference else None,
            "context_recall": dict(user_input=question, retrieved_contexts=contexts, reference=reference)
            if reference else None,
            "factual_correctness": dict(response=response, reference=reference) if response and reference else None,
        }
        needs_reference = {"context_precision", "context_recall", "factual_correctness"}
        out: dict = {"scores": {}, "skipped": {}}
        for name, kwargs in args.items():
            if kwargs is None:
                out["skipped"][name] = "no reference" if name in needs_reference and not reference else "no answer"
                continue
            try:
                value = self.metrics[name].score(**kwargs).value
                out["scores"][name] = None if value is None or value != value else round(float(value), 4)
                if out["scores"][name] is None:
                    out["skipped"][name] = "the judge gave no score"
            except Exception as exc:  # noqa: BLE001 - a judge that cannot answer is recorded, not hidden
                out["scores"][name] = None
                out["skipped"][name] = f"judge error: {type(exc).__name__}"
        return out
