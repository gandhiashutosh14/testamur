"""Retrieval over passages: BM25 (words), dense (an embedding model), and hybrid (reciprocal rank fusion of the two).

The embedder is an interface (embed_passages, embed_query): the evaluation uses bge-small through fastembed (ONNX on
the CPU); tests use a deterministic stand-in."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

import bm25s
import numpy as np

from .schema import Passage

RRF_K = 60                  # the usual constant of reciprocal rank fusion


class Embedder(Protocol):
    name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """bge-small-en-v1.5 (MIT licence) through fastembed: ONNX on the CPU, about 70 MB, downloaded on first use."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self.name = model
        self._m = TextEmbedding(model)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return _unit(np.array(list(self._m.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _unit(np.array(next(iter(self._m.query_embed([text]))), dtype=np.float32))


class HashEmbedder:
    """A deterministic bag-of-hashed-words embedder: for tests, and as a dense baseline that knows no language."""
    name = "hashed-words"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest()[:8], 16) % self.dim] += 1.0
        return v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return _unit(np.stack([self._one(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _unit(self._one(text)[None, :])[0]


def _unit(m: np.ndarray) -> np.ndarray:
    if m.ndim == 1:
        n = np.linalg.norm(m)
        return m / n if n else m
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


@dataclass
class Hit:
    passage: Passage
    score: float


class Index:
    def __init__(self, passages: list[Passage], embedder: Embedder | None = None):
        self.passages = passages
        texts = [p.text for p in passages]
        self._bm25 = bm25s.BM25()
        self._bm25.index(bm25s.tokenize(texts, stopwords="en", show_progress=False), show_progress=False)
        self._embedder = embedder
        self._vectors = embedder.embed_passages(texts) if embedder is not None else None

    def bm25(self, query: str, k: int) -> list[Hit]:
        k = min(k, len(self.passages))
        if k == 0:
            return []
        tokens = bm25s.tokenize([query], stopwords="en", show_progress=False)
        if not tokens.vocab:                                  # every word a stopword: nothing to rank by
            return [Hit(p, 0.0) for p in self.passages[:k]]
        ids, scores = self._bm25.retrieve(tokens, k=k, show_progress=False)
        return [Hit(self.passages[int(i)], float(s)) for i, s in zip(ids[0], scores[0], strict=True)]

    def dense(self, query: str, k: int) -> list[Hit]:
        if self._vectors is None:
            raise ValueError("this index has no embedder")
        sims = self._vectors @ self._embedder.embed_query(query)
        order = np.argsort(-sims, kind="stable")[:min(k, len(self.passages))]
        return [Hit(self.passages[int(i)], float(sims[i])) for i in order]

    def hybrid(self, query: str, k: int, depth: int = 50) -> list[Hit]:
        """Reciprocal rank fusion of BM25 and dense: a passage near the top of either list rises."""
        if self._vectors is None:
            return self.bm25(query, k)
        fused: dict[str, float] = {}
        for ranking in (self.bm25(query, depth), self.dense(query, depth)):
            for rank, h in enumerate(ranking):
                fused[h.passage.id] = fused.get(h.passage.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        by_id = {p.id: p for p in self.passages}
        best = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        return [Hit(by_id[pid], score) for pid, score in best]

    def search(self, method: str, query: str, k: int) -> list[Hit]:
        return {"bm25": self.bm25, "dense": self.dense, "hybrid": self.hybrid}[method](query, k)
