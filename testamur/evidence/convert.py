"""Each dataset converted to documents of passages and tasks (schema.py), read straight from its pinned archive.

Identifiers are opaque hashes of the source's own ids: reports name tasks and documents without repeating a party's
name or a paper's title. Choices made here, and why:
- CUAD: the official test split; three categories that only ask for names and dates of the cover page are left out
  (Document Name, Parties, Agreement Date), and so are supply, manufacturing, distribution, outsourcing and reseller
  agreements, which sit closest to a domain this project keeps away from.
- ContractNLI: the official test split; a hypothesis "not mentioned" by the NDA is a task where abstaining is right.
- TAT-QA: the dev split (the test split's answers are not public); a table becomes a passage of rows.
- PubMedQA: the official test split of the expert-labelled questions; the 500 abstracts form one pool to search.
- QASPER: the dev split; a question whose annotators disagree about whether the paper answers it is left out."""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

from .schema import Document, Passage, Task
from .sources import BY_NAME, path_of

TARGET_WORDS = 150          # a passage's size: a clause or two, a paragraph, a table


def hid(*parts: str, n: int = 12) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:n]


def chunk_lines(lines: list[str], target: int = TARGET_WORDS, overlap_lines: int = 1) -> list[str]:
    """Lines grouped into passages of about `target` words, the last line of one repeated at the start of the next
    when it is short (a quote that crosses the boundary is then whole in one of them). A line longer than two
    passages is split by words, with an overlap of 20."""
    pieces: list[str] = []
    for line in (x.strip() for x in lines):
        if not line:
            continue
        words = line.split()
        while len(words) > target * 2:
            pieces.append(" ".join(words[:target]))
            words = words[target - 20:]
        pieces.append(" ".join(words))
    out, cur, fresh = [], [], 0
    for p in pieces:
        cur.append(p)
        fresh += 1
        if sum(len(x.split()) for x in cur) >= target:
            out.append("\n".join(cur))
            carry = cur[-overlap_lines:] if overlap_lines else []
            # a long line is not carried: it would double the next passage, and its split already overlaps
            cur, fresh = ([] if sum(len(x.split()) for x in carry) > target // 3 else carry), 0
    if fresh:                                     # what is left after the last full passage
        out.append("\n".join(cur))
    return out


def _doc(dataset: str, native: str, texts: list[str]) -> Document:
    did = f"{dataset}:{hid(native)}"
    return Document(did, dataset, [Passage(f"{did}#{i}", t) for i, t in enumerate(texts) if t.strip()])


# ---------------------------------------------------------------------------------------------- CUAD

CUAD_SKIP_CATEGORIES = {"Document Name", "Parties", "Agreement Date"}
CUAD_SKIP_TYPES = re.compile(r"supply|manufactur|distribut|outsourc|resell", re.I)


def cuad(root: Path) -> tuple[list[Document], list[Task]]:
    z = zipfile.ZipFile(path_of(root, BY_NAME["cuad"]))
    data = json.loads(z.read("test.json"))["data"]
    docs, tasks = [], []
    for item in data:
        if CUAD_SKIP_TYPES.search(item["title"]):
            continue
        para = item["paragraphs"][0]
        doc = _doc("cuad", item["title"], chunk_lines(para["context"].split("\n")))
        docs.append(doc)
        for qa in para["qas"]:
            m = re.search(r'related to "([^"]+)"', qa["question"])
            category = m.group(1) if m else "?"
            if category in CUAD_SKIP_CATEGORIES:
                continue
            answers = list(dict.fromkeys(a["text"].strip() for a in qa["answers"] if a["text"].strip()))
            answerable = bool(answers) and not qa.get("is_impossible", False)
            tasks.append(Task(f"cuad:{hid(qa['id'])}", "cuad", "legal", "extract", qa["question"], [doc.id],
                              answers if answerable else [], None, answers if answerable else [], answerable,
                              " ... ".join(answers) if answerable else "", {"category": category}))
    return docs, tasks


# ---------------------------------------------------------------------------------------------- ContractNLI

NLI_LABELS = {"Entailment": "entailment", "Contradiction": "contradiction", "NotMentioned": "not_mentioned"}


def contractnli(root: Path) -> tuple[list[Document], list[Task]]:
    z = zipfile.ZipFile(path_of(root, BY_NAME["contractnli"]))
    data = json.loads(z.read("contract-nli/test.json"))
    hypotheses = data["labels"]
    docs, tasks = [], []
    for d in data["documents"]:
        spans = [d["text"][a:b].strip() for a, b in d["spans"]]
        doc = _doc("contractnli", str(d["id"]), chunk_lines(spans, overlap_lines=0))
        docs.append(doc)
        for key, ann in d["annotation_sets"][0]["annotations"].items():
            label = NLI_LABELS[ann["choice"]]
            evidence = [spans[i] for i in ann["spans"] if spans[i]]
            hypothesis = hypotheses[key]["hypothesis"]
            reference = "" if label == "not_mentioned" else (
                f"{'The NDA states' if label == 'entailment' else 'The NDA contradicts'} that {hypothesis[0].lower()}"
                f"{hypothesis[1:]} " + " ".join(evidence))
            tasks.append(Task(f"contractnli:{hid(str(d['id']), key)}", "contractnli", "legal", "nli",
                              hypothesis, [doc.id], [], label, evidence, label != "not_mentioned", reference,
                              {"hypothesis": key}))
    return docs, tasks


# ---------------------------------------------------------------------------------------------- TAT-QA

def _table_rows(table: list[list[str]]) -> list[str]:
    return [" | ".join(c.strip() for c in row) for row in table if any(c.strip() for c in row)]


def tatqa(root: Path) -> tuple[list[Document], list[Task]]:
    items = json.loads(path_of(root, BY_NAME["tatqa"]).read_text(encoding="utf-8"))
    docs, tasks = [], []
    for item in items:
        rows = _table_rows(item["table"]["table"])
        header, body = rows[:1], rows[1:]
        tables = []
        for i in range(0, max(1, len(body)), 25):                # a long table: header repeated on each part
            tables.append("Table\n" + "\n".join(header + body[i:i + 25]))
        paragraphs = {str(p["order"]): p["text"] for p in item["paragraphs"]}
        texts = tables + [paragraphs[k] for k in sorted(paragraphs, key=int)]
        doc = _doc("tatqa", item["table"]["uid"], texts)
        docs.append(doc)
        for q in item["questions"]:
            answer = q["answer"]
            answers = [", ".join(str(a) for a in answer)] if isinstance(answer, list) else [str(answer)]
            evidence = [paragraphs[k] for k in q.get("rel_paragraphs", []) if k in paragraphs]
            if q["answer_from"] in ("table", "table-text"):
                evidence += tables
            scale = q.get("scale") or ""
            reference = f"{answers[0]} {scale}".strip() + (f", computed as {q['derivation']}" if q.get("derivation")
                                                           else "")
            tasks.append(Task(f"tatqa:{hid(q['uid'])}", "tatqa", "finance", "answer", q["question"], [doc.id],
                              answers, None, evidence, True, reference,
                              {"answer_type": q["answer_type"], "answer_from": q["answer_from"], "scale": scale,
                               "spans": [str(a) for a in answer] if isinstance(answer, list) else [str(answer)]}))
    return docs, tasks


# ---------------------------------------------------------------------------------------------- PubMedQA

PUBMEDQA_POOL = "pubmedqa:*"            # a task's documents: every abstract of the test split, one pool


def pubmedqa(root: Path) -> tuple[list[Document], list[Task]]:
    data = json.loads(path_of(root, BY_NAME["pubmedqa"]).read_text(encoding="utf-8"))
    test = json.loads(path_of(root, BY_NAME["pubmedqa-test"]).read_text(encoding="utf-8"))
    docs, tasks = [], []
    for pmid in sorted(test):
        item = data[pmid]
        doc = _doc("pubmedqa", pmid, item["CONTEXTS"])
        docs.append(doc)
        tasks.append(Task(f"pubmedqa:{hid(pmid)}", "pubmedqa", "biomedical", "yesno", item["QUESTION"],
                          [PUBMEDQA_POOL], [item["final_decision"]], item["final_decision"], item["CONTEXTS"], True,
                          item["LONG_ANSWER"], {"document": doc.id}))
    return docs, tasks


# ---------------------------------------------------------------------------------------------- QASPER

def _qasper_answer(a: dict) -> str | None:
    if a["unanswerable"]:
        return None
    if a["yes_no"] is not None:
        return "yes" if a["yes_no"] else "no"
    if a["extractive_spans"]:
        return ", ".join(s.strip() for s in a["extractive_spans"])
    return (a["free_form_answer"] or "").strip() or None


def qasper(root: Path) -> tuple[list[Document], list[Task]]:
    t = tarfile.open(path_of(root, BY_NAME["qasper"]))
    papers = json.load(t.extractfile("qasper-dev-v0.3.json"))
    docs, tasks = [], []
    for pid, p in papers.items():
        texts = []
        for section in p["full_text"]:
            name = (section.get("section_name") or "").strip()
            for para in section["paragraphs"]:
                if para.strip():
                    texts += chunk_lines([f"{name}: {para}" if name else para], target=220)
        for fig in p.get("figures_and_tables") or []:
            if (fig.get("caption") or "").strip():
                texts.append(f"{fig.get('file', 'Figure')}: {fig['caption'].strip()}")
        doc = _doc("qasper", pid, texts)
        docs.append(doc)
        for q in p["qas"]:
            given = [_qasper_answer(a["answer"]) for a in q["answers"]]
            if not given or (any(g is None for g in given) and not all(g is None for g in given)):
                continue                                      # annotators disagree whether it is answered
            answerable = given[0] is not None
            answers = list(dict.fromkeys(g for g in given if g))
            evidence = list(dict.fromkeys(e for a in q["answers"] for e in a["answer"]["evidence"]
                                          if e and not e.startswith("FLOAT SELECTED")))
            tasks.append(Task(f"qasper:{hid(pid, q['question_id'])}", "qasper", "science", "answer", q["question"],
                              [doc.id], answers, None, evidence, answerable, answers[0] if answers else "",
                              {"kinds": sorted({("yes_no" if a["answer"]["yes_no"] is not None else
                                                 "extractive" if a["answer"]["extractive_spans"] else
                                                 "unanswerable" if a["answer"]["unanswerable"] else "free_form")
                                                for a in q["answers"]})}))
    return docs, tasks


CONVERTERS = {"cuad": cuad, "contractnli": contractnli, "tatqa": tatqa, "pubmedqa": pubmedqa, "qasper": qasper}
DOMAINS = {"cuad": "legal", "contractnli": "legal", "tatqa": "finance", "pubmedqa": "biomedical", "qasper": "science"}
