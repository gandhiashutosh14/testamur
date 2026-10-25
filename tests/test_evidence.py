"""Evidence QA: the converters (on small archives in each dataset's own format), the sample, retrieval, the answer's
contract and quote check, the ground-truth metrics, the RAGAS adapter, and the run end to end, all without a
language model or a download."""
import io
import json
import re
import tarfile
import zipfile

import pytest
from schema_fake import SchemaModel

from testamur.evidence import convert, sample
from testamur.evidence import metrics as m
from testamur.evidence.answer import Answer, answer, schema
from testamur.evidence.retrieve import HashEmbedder, Index
from testamur.evidence.run import auroc, by_dataset, evaluate, merge, render
from testamur.evidence.schema import Passage, Task
from testamur.evidence.sources import BY_NAME, path_of
from testamur.modeltier import Cassette, Reply

CLAUSE = "This Agreement may not be assigned by either party without the prior written consent of the other party."


def _write(root, name, data: bytes):
    p = path_of(root, BY_NAME[name])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    return buf.getvalue()


@pytest.fixture
def corpus(tmp_path):
    """Each dataset's own format, a few records each."""
    context = "LICENSE AGREEMENT\nThe Licensor grants a licence to use the software.\n" + CLAUSE + \
              "\nThis Agreement is governed by the laws of the State of New York."
    qas = [{"id": "lic__Anti-Assignment", "is_impossible": False,
            "question": 'Highlight the parts (if any) of this contract related to "Anti-Assignment" that should be '
                        'reviewed by a lawyer. Details: Is consent required to assign?',
            "answers": [{"text": CLAUSE, "answer_start": context.index(CLAUSE)}]},
           {"id": "lic__Non-Compete", "is_impossible": True, "answers": [],
            "question": 'Highlight the parts (if any) of this contract related to "Non-Compete" that should be '
                        'reviewed by a lawyer. Details: Is there a restriction on competing?'},
           {"id": "lic__Parties", "is_impossible": False, "answers": [{"text": "Licensor", "answer_start": 4}],
            "question": 'Highlight the parts (if any) of this contract related to "Parties" that should be reviewed '
                        'by a lawyer. Details: The two parties.'}]
    cuad = {"version": "v1", "data": [{"title": "ACME_2019_EX-10_License Agreement",
                                       "paragraphs": [{"context": context, "qas": qas}]},
                                      {"title": "ACME_2019_EX-10_Supply Agreement",
                                       "paragraphs": [{"context": "SUPPLY", "qas": []}]}]}
    _write(tmp_path, "cuad", _zip({"test.json": json.dumps(cuad)}))
    text = "Recipient shall keep the information confidential. Recipient may share it with its employees."
    nli = {"labels": {"nda-1": {"short_description": "Sharing", "hypothesis": "Receiving Party may share some "
                                "Confidential Information with some of Receiving Party's employees."},
                      "nda-2": {"short_description": "Reverse", "hypothesis": "Receiving Party shall not reverse "
                                "engineer any objects."}},
           "documents": [{"id": 7, "file_name": "x", "text": text,
                          "spans": [[0, text.index(".") + 1], [text.index(".") + 2, len(text)]],
                          "document_type": "nda", "url": "",
                          "annotation_sets": [{"annotations": {"nda-1": {"choice": "Entailment", "spans": [1]},
                                                               "nda-2": {"choice": "NotMentioned", "spans": []}}}]}]}
    _write(tmp_path, "contractnli", _zip({"contract-nli/test.json": json.dumps(nli)}))
    tat = [{"table": {"uid": "t1", "table": [["", "2019", "2018"], ["Revenue", "1,200", "1,000"]]},
            "paragraphs": [{"uid": "p", "order": 1, "text": "Revenue grew because of new customers in Asia."}],
            "questions": [{"uid": "q1", "order": 1, "question": "What was the change in revenue?", "answer": 200,
                           "derivation": "1,200 - 1,000", "answer_type": "arithmetic", "answer_from": "table",
                           "rel_paragraphs": [], "req_comparison": False, "scale": "thousand"},
                          {"uid": "q2", "order": 2, "question": "Why did revenue grow?",
                           "answer": ["new customers in Asia"], "derivation": "", "answer_type": "span",
                           "answer_from": "text", "rel_paragraphs": ["1"], "req_comparison": False, "scale": ""}]}]
    _write(tmp_path, "tatqa", json.dumps(tat).encode())
    pubmed = {"111": {"QUESTION": "Does drug A lower blood pressure?", "CONTEXTS": ["Drug A lowered pressure in "
                      "a trial of 300 patients.", "No harms were seen."], "LABELS": ["RESULTS", "RESULTS"],
                      "final_decision": "yes", "LONG_ANSWER": "Drug A lowers blood pressure."},
              "222": {"QUESTION": "Is B safe?", "CONTEXTS": ["B caused rashes in some children."],
                      "LABELS": ["RESULTS"], "final_decision": "no", "LONG_ANSWER": "B is not safe."}}
    _write(tmp_path, "pubmedqa", json.dumps(pubmed).encode())
    _write(tmp_path, "pubmedqa-test", json.dumps({"111": "yes", "222": "no"}).encode())

    def ans(**kw):
        base = {"unanswerable": False, "extractive_spans": [], "yes_no": None, "free_form_answer": "",
                "evidence": [], "highlighted_evidence": []}
        return {"answer": base | kw, "annotation_id": "a", "worker_id": "w"}
    paper = {"title": "T", "abstract": "A", "figures_and_tables": [{"file": "1-Table1-1.png", "caption": "Scores."}],
             "full_text": [{"section_name": "Data", "paragraphs": ["We use the Penn Treebank for training."]}],
             "qas": [{"question": "Which corpus is used?", "question_id": "q1",
                      "answers": [ans(extractive_spans=["Penn Treebank"],
                                      evidence=["We use the Penn Treebank for training."])]},
                     {"question": "Is it fast?", "question_id": "q2", "answers": [ans(unanswerable=True)]},
                     {"question": "Disputed?", "question_id": "q3", "answers": [ans(unanswerable=True),
                                                                                ans(yes_no=True)]}]}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = json.dumps({"1909.0001": paper}).encode()
        info = tarfile.TarInfo("qasper-dev-v0.3.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    _write(tmp_path, "qasper", buf.getvalue())
    return tmp_path


def test_each_dataset_converts_with_opaque_ids_and_its_own_rules(corpus):
    docs, tasks = convert.cuad(corpus)
    assert len(docs) == 1                                       # the supply agreement is left out
    assert {t.meta["category"] for t in tasks} == {"Anti-Assignment", "Non-Compete"}     # Parties left out
    by_cat = {t.meta["category"]: t for t in tasks}
    assert by_cat["Anti-Assignment"].answerable and by_cat["Anti-Assignment"].answers == [CLAUSE]
    assert not by_cat["Non-Compete"].answerable and by_cat["Non-Compete"].answers == []
    assert all(re.fullmatch(r"cuad:[0-9a-f]{12}", t.id) for t in tasks) and "ACME" not in docs[0].id
    _, nli = convert.contractnli(corpus)
    labels = {t.meta["hypothesis"]: t for t in nli}
    assert labels["nda-1"].label == "entailment" and labels["nda-1"].evidence == ["Recipient may share it with its "
                                                                                  "employees."]
    assert labels["nda-2"].label == "not_mentioned" and not labels["nda-2"].answerable
    tdocs, tat = convert.tatqa(corpus)
    assert tdocs[0].passages[0].text.startswith("Table\n | 2019 | 2018") and len(tat) == 2
    change = next(t for t in tat if t.meta["answer_type"] == "arithmetic")
    assert change.answers == ["200"] and "1,200 - 1,000" in change.reference
    pdocs, pm = convert.pubmedqa(corpus)
    assert len(pdocs) == 2 and all(t.documents == [convert.PUBMEDQA_POOL] for t in pm)
    _, qa = convert.qasper(corpus)
    assert len(qa) == 2                                          # the disputed question is left out
    assert {t.answerable for t in qa} == {True, False}


def test_passages_hold_about_their_size_and_overlap_by_a_line():
    short = [f"line {i} " + "word " * 10 for i in range(20)]
    chunks = convert.chunk_lines(short, target=40)
    assert len(chunks) > 3 and all(len(c.split()) <= 60 for c in chunks)
    assert chunks[0].split("\n")[-1] == chunks[1].split("\n")[0]          # a short line repeated across the boundary
    paragraphs = [f"para {i} " + "word " * 40 for i in range(10)]
    chunks = convert.chunk_lines(paragraphs, target=100)
    assert all(len(c.split()) <= 140 for c in chunks)
    assert chunks[0].split("\n")[-1] != chunks[1].split("\n")[0]          # a long one is not carried
    long = convert.chunk_lines(["w " * 1000], target=100)
    assert len(long) > 4 and all(len(c.split()) <= 200 for c in long)


def test_the_sample_is_stratified_spread_and_the_same_every_time():
    tasks = [Task(f"d:{i}", "d", "x", "nli", "q", [f"doc{i % 7}"], [], ["entailment", "not_mentioned"][i % 2])
             for i in range(60)]
    picked = sample.draw(tasks, lambda t: t.label, {"entailment": 3, "not_mentioned": 3}, per_document=1)
    assert len(picked) == 6 and len({t.documents[0] for t in picked}) == 6
    assert [t.id for t in picked] == [t.id for t in sample.draw(tasks, lambda t: t.label,
                                                                {"entailment": 3, "not_mentioned": 3}, 1)]
    with pytest.raises(ValueError):
        sample.draw(tasks, lambda t: t.label, {"entailment": 9}, per_document=1)


def test_the_committed_sample_has_its_quotas():
    manifest = json.loads(open("evals/evidence/manifest.json", encoding="utf-8").read())
    assert {k: len(v["tasks"]) for k, v in manifest["datasets"].items()} == {d: 32 for d in sample.QUOTAS}
    for name, (_, quotas, _) in sample.QUOTAS.items():
        assert manifest["datasets"][name]["strata"] == quotas


def test_retrieval_finds_by_words_by_meaning_and_both():
    ps = [Passage("d#0", "The weather was sunny all week."), Passage("d#1", CLAUSE),
          Passage("d#2", "Payments are due within thirty days of invoice.")]
    ix = Index(ps, HashEmbedder())
    for method in ("bm25", "dense", "hybrid"):
        assert ix.search(method, "may the agreement be assigned without consent", 1)[0].passage.id == "d#1", method
    assert len(ix.search("bm25", "the a an", 2)) == 2                    # all stopwords: no crash


def test_the_ground_truth_metrics_are_the_datasets_own():
    assert m.token_f1("the Penn Treebank", "Penn Treebank") == 1.0
    assert m.jaccard(CLAUSE, CLAUSE + " Assignment in breach is void.") >= 0.5
    assert m.numbers("a fall of (1,234) to 1.2 million") == [-1234.0, 1.2e6]
    assert m.number_matches("revenue rose by 200", 200, "thousand")
    assert m.number_matches("an increase of $200,000", 200, "thousand")
    assert not m.number_matches("it fell by 200", -200, "")
    meta = {"answer_type": "arithmetic", "scale": "thousand"}
    assert m.correct("answer", "tatqa", True, ["200"], None, meta, "200 thousand", None, False)
    assert m.correct("extract", "cuad", False, [], None, {}, "", None, True)                # no clause: abstain
    assert not m.correct("extract", "cuad", True, [CLAUSE], None, {}, "", None, True)
    assert m.correct("nli", "contractnli", True, [], "entailment", {}, "", "entailment", False)
    assert m.correct("answer", "qasper", True, ["yes"], None, {}, "Yes, it is.", None, False)
    assert m.holds("We use the Penn Treebank for training.", "Penn Treebank")
    assert m.rank_of_evidence(["nothing", "the Penn Treebank"], ["Penn Treebank"]) == 2


def test_the_answer_cites_given_passages_and_its_quotes_are_checked():
    ps = [Passage("d#0", CLAUSE), Passage("d#1", "Payments are due within thirty days of invoice.")]
    task = Task("cuad:x", "cuad", "legal", "extract", "Assignment?", ["d"], [CLAUSE], evidence=[CLAUSE])
    sch = schema(task, [p.id for p in ps])
    assert sch["properties"]["citations"]["items"]["properties"]["passage"]["enum"] == ["d#0", "d#1"]
    assert list(sch["properties"]) == ["citations", "answer", "abstain"]                   # citations first

    def fill(quote):
        return SchemaModel(lambda msgs, s, out: {"citations": [{"passage": "d#0", "quote": quote}],
                                                 "answer": CLAUSE, "abstain": False})
    good = answer(fill(CLAUSE[:60]), task, ps)
    assert good.supported and good.with_rule("extract") == (CLAUSE, None, False)
    forged = answer(fill("This Agreement may be assigned freely by either party."), task, ps)
    assert not forged.supported and forged.with_rule("extract") == ("", None, True)
    unsupported_nli = Answer("", "entailment", False, [{"passage": "d#0", "quote": "invented words here"}])
    assert unsupported_nli.with_rule("nli") == ("", "not_mentioned", True)

    class Broken:
        model_id = "broken"

        def chat(self, messages, max_tokens=200, schema=None):
            return Reply("not json", 1, 1)
    assert answer(Broken(), task, ps).error == "not JSON"


def test_ragas_runs_through_our_model_client_and_skips_what_does_not_apply():
    pytest.importorskip("ragas")
    from testamur.evidence.judge import Judge
    j = Judge(SchemaModel(), HashEmbedder())
    full = j.score("Is consent needed to assign?", CLAUSE, [CLAUSE], CLAUSE)
    assert set(full["scores"]) == {"faithfulness", "answer_relevancy", "context_precision", "context_recall",
                                   "factual_correctness"} and not full["skipped"]
    assert all(v is None or 0.0 <= v <= 1.0 for v in full["scores"].values())
    none = j.score("Is consent needed?", None, [CLAUSE], "")
    assert set(none["skipped"]) == set(full["scores"]) and none["scores"] == {}

    class Failing(SchemaModel):
        def chat(self, messages, max_tokens=200, schema=None):
            raise TimeoutError("judge timed out")
    failed = Judge(Failing(), HashEmbedder()).score("q", "an answer here", [CLAUSE], CLAUSE)
    assert all(v is None for v in failed["scores"].values())
    assert all(r.startswith("judge error") for r in failed["skipped"].values())


def test_the_run_scores_records_and_replays(corpus, tmp_path):
    pytest.importorskip("ragas")
    from testamur.evidence.judge import Judge
    sample.build(corpus)
    tasks = {}
    for name in sample.QUOTAS:
        tasks[name] = [json.loads(x)["id"] for x in (corpus / "normalized" / f"{name}.tasks.jsonl")
                       .read_text(encoding="utf-8").splitlines()]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"datasets": {k: {"tasks": v} for k, v in tasks.items()}}), encoding="utf-8")

    def quote_first(msgs, s, out):
        if "citations" in out:
            pid, line = re.search(r"\[([^\]]+)\]\n(.+)", msgs[-1]["content"]).groups()
            out["citations"] = [{"passage": pid, "quote": line[:80]}]
            out["answer"] = line[:80]
        return out
    rec_a, rec_j = tmp_path / "answers.jsonl", tmp_path / "judge.jsonl"
    live = evaluate(manifest, corpus, Cassette(rec_a, SchemaModel(quote_first)),
                    Judge(Cassette(rec_j, SchemaModel()), HashEmbedder()), HashEmbedder())
    again = evaluate(manifest, corpus, Cassette(rec_a), Judge(Cassette(rec_j), HashEmbedder()), HashEmbedder())
    strip = [{k: v for k, v in r.items() if k not in ("seconds", "judge")} for r in live["tasks"]]
    assert strip == [{k: v for k, v in r.items() if k not in ("seconds", "judge")} for r in again["tasks"]]
    summary = by_dataset(merge([live]))
    assert summary["all"]["tasks"] == len(live["tasks"]) and "ragas" in summary["all"]
    text = render(merge([live]), {"generated": "now", "commit": "c", "command": "test"})
    assert "## Retrieval" in text and "## How far the judge can be trusted" in text and "CC BY 4.0" in text
    with pytest.raises(ValueError):
        merge([live, {**live, "manifest": "other"}])


def test_auroc_is_the_chance_a_right_answer_outscores_a_wrong_one():
    assert auroc([0.9, 0.8, 0.2], [True, True, False]) == 1.0
    assert auroc([0.5, 0.5], [True, False]) == 0.5
    assert auroc([0.1, 0.9], [True, False]) == 0.0
    assert auroc([0.3], [True]) is None
