"""The fixed sample the evaluation runs on: stratified per dataset, so that abstaining and every label are measured,
and spread over documents, drawn by a seeded hash (no random state), and committed before any model runs on it."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .convert import CONVERTERS, hid
from .schema import Task, read_tasks, write_jsonl

PER_DATASET = 32
SEED = "2026-10-25"
# per dataset: (stratum of a task, quota per stratum, most tasks per document)
QUOTAS = {
    "cuad": (lambda t: "answerable" if t.answerable else "not_answerable", {"answerable": 16, "not_answerable": 16}, 1),
    "contractnli": (lambda t: t.label, {"entailment": 11, "contradiction": 11, "not_mentioned": 10}, 1),
    "tatqa": (lambda t: t.meta["answer_type"], {"arithmetic": 14, "span": 10, "multi-span": 6, "count": 2}, 1),
    "pubmedqa": (lambda t: t.label, {"yes": 12, "no": 12, "maybe": 8}, 1),
    "qasper": (lambda t: "answerable" if t.answerable else "not_answerable", {"answerable": 24, "not_answerable": 8},
               1),
}


def build(root: str | Path) -> dict[str, dict]:
    """Convert every dataset into data/corpus/normalized/<name>.{documents,tasks}.jsonl."""
    root, stats = Path(root), {}
    for name, convert in CONVERTERS.items():
        docs, tasks = convert(root)
        write_jsonl(root / "normalized" / f"{name}.documents.jsonl", docs)
        write_jsonl(root / "normalized" / f"{name}.tasks.jsonl", tasks)
        stats[name] = {"documents": len(docs), "passages": sum(len(d.passages) for d in docs), "tasks": len(tasks),
                       "answerable": sum(t.answerable for t in tasks)}
    return stats


def draw(tasks: list[Task], stratum, quotas: dict[str, int], per_document: int, seed: str = SEED) -> list[Task]:
    picked, used, filled = [], Counter(), Counter()
    for t in sorted(tasks, key=lambda t: hid(seed, t.id, n=16)):
        s, doc = stratum(t), (t.meta.get("document") or t.documents[0])
        if filled[s] >= quotas.get(s, 0) or used[doc] >= per_document:
            continue
        picked.append(t)
        filled[s] += 1
        used[doc] += 1
    short = {s: q - filled[s] for s, q in quotas.items() if filled[s] < q}
    if short:
        raise ValueError(f"not enough tasks for the quotas: {short}")
    return sorted(picked, key=lambda t: t.id)


def manifest(root: str | Path) -> dict:
    root = Path(root)
    out = {"seed": SEED, "per_dataset": PER_DATASET, "datasets": {}}
    for name, (stratum, quotas, per_document) in QUOTAS.items():
        tasks = list(read_tasks(root / "normalized" / f"{name}.tasks.jsonl").values())
        picked = draw(tasks, stratum, quotas, per_document)
        out["datasets"][name] = {"strata": {s: sum(stratum(t) == s for t in picked) for s in quotas},
                                 "tasks": [t.id for t in picked]}
    return out


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
