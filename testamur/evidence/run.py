"""The evaluation: every task of the committed sample (evals/evidence/manifest.json) through retrieval, the answer and
its quote check, scored against the dataset's ground truth, and by RAGAS with a judge of another model family.

Per task: the rank at which each retriever (BM25, dense, hybrid) first returns the gold evidence; the answer from the
hybrid's top passages; right or wrong by the dataset's own criterion (metrics.py), as the model answered and under the
evidence rule (answer.py); and the five RAGAS metrics where they apply (judge.py). Per dataset and overall: accuracy,
confidently wrong answers, abstention where abstaining is right and where it is not, retrieval hit rates and mean
reciprocal rank, the RAGAS means, and how well each RAGAS metric separates right answers from wrong ones (AUROC), as
the measure of how far a small judge can be trusted. Sharded like the holdout; passage ids are hashes of the
sources' own ids, so a task's prompt is the same in any shard and a recording replays under any sharding."""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path

from . import metrics as m
from .answer import answer
from .convert import PUBMEDQA_POOL
from .retrieve import Index
from .schema import Document, Passage, Task, read_documents, read_tasks

K = 4                         # passages given to the model
DEPTH = 10                    # how deep retrieval is scored
METHODS = ("bm25", "dense", "hybrid")


def manifest_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


class Corpus:
    def __init__(self, root: str | Path, datasets: list[str]):
        root = Path(root) / "normalized"
        self.documents: dict[str, Document] = {}
        self.tasks: dict[str, Task] = {}
        for name in datasets:
            self.documents |= read_documents(root / f"{name}.documents.jsonl")
            self.tasks |= read_tasks(root / f"{name}.tasks.jsonl")

    def scope(self, task: Task) -> list[Passage]:
        ids = [d for d in self.documents if d.startswith("pubmedqa:")] if task.documents == [PUBMEDQA_POOL] \
            else task.documents
        return [p for d in ids for p in self.documents[d].passages]


VERDICT_WORDS = {"entailment": "The document supports the statement.",
                 "contradiction": "The document contradicts the statement.",
                 "not_mentioned": "The document does not mention it."}


def said(kind: str, label: str | None, text: str) -> str:
    """The answer as a sentence, the way RAGAS's judge reads responses: a label task's label in words, not a code."""
    if kind == "nli":
        return f"{VERDICT_WORDS.get(label, '')} {text}".strip()
    if kind == "yesno" and label:
        return f"{label.capitalize()}. {text}" if text else f"{label.capitalize()}."
    return text


def _evidence_rank(task: Task, ranked: list[Passage]) -> int | None:
    if task.documents == [PUBMEDQA_POOL]:
        gold = task.meta["document"] + "#"
        return next((i for i, p in enumerate(ranked, 1) if p.id.startswith(gold)), None)
    return m.rank_of_evidence([p.text for p in ranked], task.evidence)


def evaluate(manifest_path: str | Path, corpus_root: str | Path, generator, judge=None, embedder=None,
             shard: tuple[int, int] = (0, 1), limit: int | None = None) -> dict:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    ids = sorted(i for d in manifest["datasets"].values() for i in d["tasks"])
    mine = ids[shard[0]::shard[1]][:limit]
    corpus = Corpus(corpus_root, sorted({i.split(":")[0] for i in mine}))
    indexes: dict[tuple, Index] = {}
    rows = []
    for tid in mine:
        task = corpus.tasks[tid]
        key = tuple(task.documents)
        t0 = time.perf_counter()
        if key not in indexes:
            indexes[key] = Index(corpus.scope(task), embedder)
        index = indexes[key]
        ranks = {}
        for method in METHODS if embedder is not None else ("bm25",):
            ranks[method] = _evidence_rank(task, [h.passage for h in index.search(method, task.question, DEPTH)])
        passages = [h.passage for h in index.search("hybrid" if embedder is not None else "bm25", task.question, K)]
        t_retrieve = time.perf_counter() - t0
        a = answer(generator, task, passages)
        raw = (a.answer, a.label, a.abstained)
        ruled = a.with_rule(task.kind)
        row = {"task": tid, "dataset": task.dataset, "domain": task.domain, "kind": task.kind,
               "answerable": task.answerable, "gold_label": task.label, "ranks": ranks,
               "answer": {"label": a.label, "abstained": a.abstained, "supported": a.supported,
                          "citations": len(a.citations), "valid_citations": len(a.valid), "error": a.error,
                          "chars": len(a.answer)},
               "correct": {"as_answered": m.correct(task.kind, task.dataset, task.answerable, task.answers, task.label,
                                                    task.meta, *raw),
                           "evidence_rule": m.correct(task.kind, task.dataset, task.answerable, task.answers,
                                                      task.label, task.meta, *ruled)},
               "abstained_under_rule": ruled[2],
               "f1": round(m.answer_f1(a.answer, task.answers), 4) if task.answers and task.kind != "nli" else None,
               "tokens": {"prompt": a.prompt_tokens, "completion": a.completion_tokens},
               "seconds": {"retrieve": round(t_retrieve, 2), "answer": round(a.seconds, 2)}}
        if judge is not None:
            before = (judge.llm.calls, judge.llm.prompt_tokens + judge.llm.completion_tokens, judge.llm.seconds)
            response = None if a.abstained or not a.answer else said(task.kind, a.label, a.answer)
            row["ragas"] = judge.score(task.question, response, [p.text for p in passages], task.reference)
            row["judge"] = {"calls": judge.llm.calls - before[0],
                            "tokens": judge.llm.prompt_tokens + judge.llm.completion_tokens - before[1],
                            "seconds": round(judge.llm.seconds - before[2], 2)}
        rows.append(row)
    from ..harness import git_stamp
    return {"manifest": manifest_digest(manifest_path), "shard": list(shard), "commit": git_stamp(Path.cwd()),
            "generator": {"model": getattr(generator, "model_id", None),
                          "fingerprint": getattr(generator, "fingerprint", None)},
            "judge": None if judge is None else {"model": judge.model_id, "fingerprint": judge.fingerprint},
            "embedder": getattr(embedder, "name", None), "k": K, "tasks": rows}


# ---------------------------------------------------------------------------------------------- the numbers

def merge(parts: list[dict]) -> dict:
    for key in ("manifest", "commit"):
        values = {p[key] for p in parts}
        if len(values) > 1:
            raise ValueError(f"results of different {key}s: {sorted(values)}")
    for key in ("generator", "judge"):
        values = {json.dumps(p[key], sort_keys=True) for p in parts}
        if len(values) > 1:
            raise ValueError(f"results of different {key}s")
    first = parts[0]
    return {k: first[k] for k in ("manifest", "commit", "generator", "judge", "embedder", "k")} | {
        "tasks": sorted((r for p in parts for r in p["tasks"]), key=lambda r: r["task"])}


def auroc(scores: list[float], positive: list[bool]) -> float | None:
    """The probability a random positive scores above a random negative (ties count half); None without both."""
    pos = [s for s, y in zip(scores, positive, strict=True) if y]
    neg = [s for s, y in zip(scores, positive, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    out: dict = {"tasks": n}
    for mode in ("as_answered", "evidence_rule"):
        right = sum(r["correct"][mode] for r in rows)
        abstained = [r["answer"]["abstained"] if mode == "as_answered" else r["abstained_under_rule"] for r in rows]
        wrong_answered = sum(not r["correct"][mode] and not a for r, a in zip(rows, abstained, strict=True))
        out[mode] = {"right": right, "wrong_answers": wrong_answered,
                     "abstained": sum(abstained),
                     "abstained_where_right": sum(a for r, a in zip(rows, abstained, strict=True) if not r["answerable"]),
                     "not_answerable": sum(not r["answerable"] for r in rows)}
    out["supported"] = sum(r["answer"]["supported"] for r in rows if not r["answer"]["abstained"])
    out["answered"] = sum(not r["answer"]["abstained"] for r in rows)
    out["errors"] = sum(bool(r["answer"]["error"]) for r in rows)
    retrieval = {}
    for method in METHODS:
        ranks = [r["ranks"].get(method) for r in rows if method in r["ranks"] and r["answerable"]]
        if ranks:
            retrieval[method] = {"n": len(ranks), "hit@1": sum(x == 1 for x in ranks if x),
                                 "hit@4": sum(x is not None and x <= K for x in ranks),
                                 "hit@10": sum(x is not None for x in ranks),
                                 "mrr@10": round(statistics.mean(1 / x if x else 0 for x in ranks), 3)}
    out["retrieval"] = retrieval
    scored = [r for r in rows if r.get("ragas")]
    if scored:
        ragas = {}
        for name in ("faithfulness", "answer_relevancy", "context_precision", "context_recall",
                     "factual_correctness"):
            pairs = [(r["ragas"]["scores"][name], r) for r in scored if r["ragas"]["scores"].get(name) is not None]
            values = [v for v, _ in pairs]
            ragas[name] = {"n": len(values), "mean": round(statistics.mean(values), 3) if values else None,
                           "auroc_vs_right": auroc(values, [r["correct"]["as_answered"] for _, r in pairs]),
                           "not_scored": sum(name in r["ragas"]["skipped"] for r in scored)}
        faith = [(r["ragas"]["scores"]["faithfulness"], r["answer"]["supported"]) for r in scored
                 if r["ragas"]["scores"].get("faithfulness") is not None]
        ragas["faithfulness"]["auroc_vs_verified_quote"] = auroc([f for f, _ in faith], [s for _, s in faith])
        out["ragas"] = ragas
        out["judge"] = {"calls": sum(r["judge"]["calls"] for r in scored),
                        "tokens": sum(r["judge"]["tokens"] for r in scored),
                        "seconds": round(sum(r["judge"]["seconds"] for r in scored), 1)}
    secs = [r["seconds"]["answer"] for r in rows]
    out["answer_seconds"] = {"p50": round(statistics.median(secs), 1) if secs else None,
                             "p95": round(sorted(secs)[int(0.95 * (len(secs) - 1))], 1) if secs else None}
    out["tokens"] = sum(r["tokens"]["prompt"] + r["tokens"]["completion"] for r in rows)
    return out


def by_dataset(merged: dict) -> dict[str, dict]:
    datasets = sorted({r["dataset"] for r in merged["tasks"]})
    return {d: summarise([r for r in merged["tasks"] if r["dataset"] == d]) for d in datasets} | {
        "all": summarise(merged["tasks"])}


# ---------------------------------------------------------------------------------------------- the report

NAMES = {"cuad": "CUAD (contract clauses)", "contractnli": "ContractNLI (NDA statements)",
         "tatqa": "TAT-QA (annual reports)", "pubmedqa": "PubMedQA (biomedical abstracts)",
         "qasper": "QASPER (scientific papers)", "all": "All"}


def _of(k, n):
    return f"{k} of {n}"


def render(merged: dict, meta: dict) -> str:
    from .sources import SOURCES
    s = by_dataset(merged)
    order = [d for d in ("cuad", "contractnli", "tatqa", "pubmedqa", "qasper") if d in s] + ["all"]
    domains = {r["dataset"]: r["domain"] for r in merged["tasks"]}
    g, j = merged["generator"], merged["judge"]
    lines = ["# Evidence QA across four domains: ground truth and RAGAS", "",
             f"Generated {meta['generated']} at commit `{meta['commit']}` with `{meta['command']}`, from the runs at "
             f"commit `{merged['commit']}`.", "",
             f"{s['all']['tasks']} tasks from public, expert-labelled datasets, a seeded and stratified sample committed "
             f"before any model ran on it (`evals/evidence/manifest.json`, digest `{merged['manifest']}`). The answers: "
             f"`{g['model']}` (fingerprint `{g['fingerprint']}`), given the hybrid retriever's top {merged['k']} "
             "passages, its decoding constrained to a JSON contract with citations. The judge for RAGAS: "
             + ((f"`{j['model']}` (fingerprint `{j['fingerprint']}`), " + ("not the model that answered" if
                 j["model"] != g["model"] else "the model that answered, grading its own answers")) if j else "none")
             + f". Embeddings: `{merged['embedder']}`. Every model call recorded; a replay reproduces every number "
             "below except the timings.", "",
             "## Right answers", "",
             "Right by each dataset's own criterion. *As answered*: the model's answer. *Evidence rule*: an answer no "
             "verified quote supports is turned into an abstention (for a statement about an NDA, into \"not "
             "mentioned\"), TESTAMUR's rule. A wrong answer is one given and wrong; an abstention is not counted as "
             "one.", "",
             "| Dataset | Domain | Right, as answered | Right, evidence rule | Wrong answers, as answered | Wrong "
             "answers, evidence rule | Abstained where right | Answers with a verified quote |",
             "|---|---|---|---|---|---|---|---|"]
    for d in order:
        x = s[d]
        a, e = x["as_answered"], x["evidence_rule"]
        lines.append(f"| {NAMES[d]} | {domains.get(d, '')} | {_of(a['right'], x['tasks'])} | "
                     f"{_of(e['right'], x['tasks'])} | {a['wrong_answers']} | {e['wrong_answers']} | "
                     f"{_of(a['abstained_where_right'], a['not_answerable']) if a['not_answerable'] else '-'} | "
                     f"{_of(x['supported'], x['answered'])} |")
    lines += ["", "## Retrieval", "",
              f"Whether the gold evidence is among the top {merged['k']} passages (the ones the model sees), and the "
              "mean reciprocal rank of its first passage in the top 10, over the answerable tasks. PubMedQA retrieves "
              "from the pool of all 500 test abstracts; the others from the task's own document.", "",
              "| Dataset | BM25 | Dense | Hybrid | Hybrid MRR@10 |", "|---|---|---|---|---|"]
    for d in order:
        r = s[d]["retrieval"]
        cell = {mt: (_of(r[mt]["hit@4"], r[mt]["n"]) if mt in r else "-") for mt in METHODS}
        mrr = r["hybrid"]["mrr@10"] if "hybrid" in r else "-"
        lines.append(f"| {NAMES[d]} | {cell['bm25']} | {cell['dense']} | {cell['hybrid']} | {mrr} |")
    if "ragas" in s["all"]:
        metric_names = ("faithfulness", "answer_relevancy", "context_precision", "context_recall",
                        "factual_correctness")
        lines += ["", "## RAGAS", "",
                  "Means over the tasks each metric applies to (a metric that needs a reference is not scored without "
                  "one; a metric that needs an answer, not on an abstention).", "",
                  "| Dataset | Faithfulness | Answer relevancy | Context precision | Context recall | Factual "
                  "correctness |", "|---|---|---|---|---|---|"]
        for d in order:
            rg = s[d].get("ragas", {})
            cells = [(f"{rg[k]['mean']} (n={rg[k]['n']})" if rg.get(k) and rg[k]["mean"] is not None else "-")
                     for k in metric_names]
            lines.append(f"| {NAMES[d]} | " + " | ".join(cells) + " |")
        ra = s["all"]["ragas"]
        lines += ["", "## How far the judge can be trusted", "",
                  "AUROC: the probability that a right answer (by the ground truth) scores higher than a wrong one, so "
                  "0.5 is a metric that cannot tell them apart. For faithfulness, also against TESTAMUR's deterministic "
                  "quote check.", "",
                  "| Metric | Scored | AUROC against right answers |", "|---|---|---|"]
        for k in metric_names:
            lines.append(f"| {k.replace('_', ' ')} | {ra[k]['n']} | {ra[k]['auroc_vs_right'] or '-'} |")
        lines += ["", f"Faithfulness against the verified-quote check: AUROC "
                      f"{ra['faithfulness'].get('auroc_vs_verified_quote') or '-'}."]
        jj = s["all"]["judge"]
        lines += ["", f"The judge made {jj['calls']:,} calls ({jj['tokens']:,} tokens, {jj['seconds']:,.0f} s on the "
                      "runners' CPUs)."]
    x = s["all"]
    lines += ["", "## Cost", "",
              f"Answers: {x['tokens']:,} tokens; {x['answer_seconds']['p50']} s per answer at the median, "
              f"{x['answer_seconds']['p95']} s at the 95th percentile, on one 4-vCPU GitHub runner per shard, no GPU. "
              "Model cost: 0 (open weights on the runner's CPU).",
              "", "## What this does and does not show", "",
              "- 32 tasks per dataset find where the system goes wrong and roughly how often; they do not rank it "
              "against published results, which use full test sets and larger models.",
              "- The sample is stratified (half CUAD's tasks have no answer in the contract, a third of ContractNLI's "
              "statements are not mentioned), so the abstention numbers mean something; the proportions are not the "
              "datasets' own.",
              "- RAGAS's numbers are a small judge's opinion; the AUROC table says how much of it agrees with the ground "
              "truth.",
              "- TAT-QA's numbers are matched with their sign and within 0.5%; QASPER's free answers by token F1, which "
              "undercounts paraphrases.",
              "", "## Sources", ""]
    for src in SOURCES:
        if src.name.endswith("-test"):
            continue
        lines.append(f"- **{src.name}** ({src.domain}, {src.licence}): {src.citation}.")
    return "\n".join(lines) + "\n"
