"""The engine end to end on a tiny born-digital world (no OCR, no model): fast enough for every push.

The OCR path and the model tier are exercised by the harness job in CI."""
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import func, select

from testamur.engine import Engine
from testamur.harness import apply_gates, load_registry, run
from testamur.model import CheckResult, Severity, Tier, Verdict
from testamur.pack import load_pack
from testamur.store import Document, ExtractionRow, Store, UploadRow, VerdictRow
from testamur.synth.world import build_world

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    out = tmp_path_factory.mktemp("world")
    world = build_world(seed=11, size="tiny", out=out, digital_only=True)
    return out, world


def test_the_same_seed_gives_the_same_bytes(tmp_path):
    """Scans and photos included: recorded model calls and committed reports depend on it."""
    def digest(out):
        build_world(seed=5, size="tiny", out=out)
        return sorted(sha256(p.read_bytes()).hexdigest() for p in (out / "files").iterdir())
    first, second = digest(tmp_path / "a"), digest(tmp_path / "b")
    assert len(first) > 10 and first == second


def test_the_tiny_world_passes_the_dangerous_direction_gates(tiny, tmp_path):
    out, _ = tiny
    report = run(out, tmp_path / "h.db", model=None)
    apply_gates(report, ROOT / "gates.yaml")
    by_name = {g["gate"]: g for g in report.gates}
    for gate in ("status false vouching", "critical check false passes", "documents matched to the wrong creditor",
                 "model PASS on hostile documents", "OCR pages paid again on re-index"):
        assert by_name[gate]["ok"], by_name[gate]
    assert report.metrics["classification"]["accuracy"] >= 0.95


def test_the_same_file_twice_is_one_document_and_one_extraction(tiny, tmp_path):
    out, world = tiny
    store = Store(f"sqlite:///{tmp_path / 'd.db'}")
    load_registry(store, world)
    engine = Engine(store, load_pack(), world.as_of, tmp_path / "blobs")
    u = next(x for x in world.uploads if x.truth.doc_type == "claim_form")
    data = (out / u.file).read_bytes()
    first = engine.process(u.tenant_id, engine.ingest(u.tenant_id, u.matter_id, "a.pdf", data, "staff_upload",
                                                      u.uploaded_at))
    second = engine.process(u.tenant_id, engine.ingest(u.tenant_id, u.matter_id, "copy.pdf", data,
                                                       "email_ingest", u.uploaded_at))
    assert not first.deduplicated and second.deduplicated and first.document_id == second.document_id
    with store.tenant(u.tenant_id) as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 1
        assert s.scalar(select(func.count()).select_from(UploadRow)) == 2
        assert s.scalar(select(func.count()).select_from(ExtractionRow)) == 1


def test_a_transient_abstain_never_replaces_a_conclusive_verdict(tiny, tmp_path):
    out, world = tiny
    store = Store(f"sqlite:///{tmp_path / 'r.db'}")
    engine = Engine(store, load_pack(), world.as_of, tmp_path / "blobs")
    with store.tenant(1) as s:
        row = VerdictRow(claim_id=1, requirement_id="authority", check_id="auth_current", document_id=7,
                         verdict="PASS", severity="critical", tier="field", engine_version=engine.version)
        s.add(row)
        s.flush()
        flaky = CheckResult(check_id="auth_current", requirement_id="authority", verdict=Verdict.ABSTAIN,
                            severity=Severity.CRITICAL, tier=Tier.FIELD, reason="timeout", transient=True)
        kept = engine._store(s, 1, 7, flaky, row)
        assert kept.verdict == Verdict.PASS and row.verdict == "PASS"
        settled = flaky.model_copy(update={"transient": False, "reason": "value unreadable"})
        assert engine._store(s, 1, 7, settled, row).verdict == Verdict.ABSTAIN   # a judgement may replace it


def test_a_hostile_document_is_never_shown_to_the_model(tiny, tmp_path):
    out, world = tiny
    store = Store(f"sqlite:///{tmp_path / 'x.db'}")

    class WouldPass:
        model_id = "scripted"
        calls = 0

        def chat(self, messages, max_tokens=200):
            WouldPass.calls += 1
            raise AssertionError("the model must not be consulted on a hostile document")

    engine = Engine(store, load_pack(), world.as_of, tmp_path / "blobs", model=WouldPass())
    with store.tenant(1) as s:
        doc = Document(sha256="cd" * 32, doc_type="facility_agreement", hostile=True)
        s.add(doc)
        s.flush()
        r = engine._model_check(s, 1, "clause_interest", "evidence_of_debt", doc, None, {})
    assert r.verdict == Verdict.ABSTAIN and "automated readers" in r.reason and WouldPass.calls == 0


def test_the_gates_fail_when_the_engine_vouches_wrongly(tiny, tmp_path):
    out, _ = tiny
    report = run(out, tmp_path / "g.db", model=None, reindex_check=False)
    report.metrics["status"]["false_vouching"] = 1
    apply_gates(report, ROOT / "gates.yaml")
    assert not report.passed
