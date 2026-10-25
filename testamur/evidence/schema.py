"""The normalized corpus every dataset is converted to: documents of passages, and tasks that point at them."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Passage:
    id: str                       # "<document id>#<n>"
    text: str


@dataclass
class Document:
    id: str                       # "<dataset>:<the source's own id>"
    dataset: str
    passages: list[Passage]


@dataclass
class Task:
    id: str                       # "<dataset>:<the source's own id>"
    dataset: str
    domain: str                   # legal | finance | biomedical | science
    kind: str                     # extract | nli | yesno | answer
    question: str
    documents: list[str]          # the documents a system may search (one, or a pool)
    answers: list[str]            # any of these is right (empty when not answerable)
    label: str | None = None      # the right label, for nli and yesno
    evidence: list[str] = field(default_factory=list)    # the passages' words the answer rests on
    answerable: bool = True       # False: the documents do not answer it, and abstaining is right
    reference: str = ""           # a reference answer in words, for the reference-based RAGAS metrics
    meta: dict = field(default_factory=dict)


LABELS = {"nli": ["entailment", "contradiction", "not_mentioned"], "yesno": ["yes", "no", "maybe"]}


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def read_documents(path: Path) -> dict[str, Document]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        out[d["id"]] = Document(d["id"], d["dataset"], [Passage(**p) for p in d["passages"]])
    return out


def read_tasks(path: Path) -> dict[str, Task]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        t = Task(**json.loads(line))
        out[t.id] = t
    return out
