"""Traces: every stage from upload to approval is a span with the identifiers that join it to the rest (tenant, claim,
document, run), and never the text of a document, a field value or a secret. A workflow pausing for a person is not
recorded as an error, and an error is recorded by its type, never its message."""
import random
from datetime import datetime, time

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from scripted import Agents, _conditional_authority, _pair
from sqlalchemy import select

from testamur import telemetry
from testamur.investigate import Investigations, sqlite_checkpointer
from testamur.store import Claim, Creditor
from testamur.synth.render import DocContent, render_pdf


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    return exporter


def test_a_span_keeps_identifiers_and_measurements_and_drops_everything_else(spans):
    with telemetry.span("stage", tenant_id=3, claim_id=9, quote="the creditor's account 0042", text="secret",
                        api_key="tsk_x") as note:
        note(tokens=120, value="Rs. 15,00,000")
    (s,) = spans.get_finished_spans()
    assert dict(s.attributes).keys() == {"testamur.tenant_id", "testamur.claim_id", "testamur.tokens", "testamur.ms"}


def test_an_error_is_recorded_by_its_type_never_its_message(spans):
    with pytest.raises(ValueError), telemetry.span("stage", tenant_id=1):
        raise ValueError("account 004200 of Kavya Rao")
    (s,) = spans.get_finished_spans()
    assert not s.status.is_ok and s.attributes["testamur.outcome"] == "error: ValueError"
    recorded = [str(v) for e in s.events for v in e.attributes.values()] + [s.status.description or ""]
    assert recorded and not any("Kavya" in r or "004200" in r for r in recorded)


def test_reading_a_document_is_traced_stage_by_stage_without_its_text(spans, env):
    world, store, engine = env
    with store.tenant(1) as s:
        c = s.scalars(select(Claim)).first()
        matter, creditor_id, legal_name = c.matter_id, c.creditor_id, s.get(Creditor, c.creditor_id).legal_name
    page = [("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", legal_name),
            ("kv", "Authorised representative", "Kavya Rao, Head of Recoveries"),
            ("kv", "Date of issue", world.as_of.isoformat())]
    uid = engine.ingest(1, matter, "a.pdf", render_pdf(DocContent([page]), random.Random(2)), "portal",
                        datetime.combine(world.as_of, time(9)), declared_creditor_id=creditor_id)
    engine.prefetch(1, uid)
    engine.process(1, uid)
    finished = spans.get_finished_spans()
    assert {"ingest", "ocr", "process", "status"} <= {s.name for s in finished}
    for s in finished:
        assert s.attributes.get("testamur.tenant_id") == 1, s.name
        assert not any(w in str(v) for v in s.attributes.values() for w in ("Kavya", legal_name)), s.name
    assert next(s for s in finished if s.name == "ingest").attributes["testamur.upload_id"] == uid


def test_an_investigation_pausing_for_a_person_is_not_an_error(spans, env, tmp_path):
    claim, doc = _conditional_authority(env)
    saver, conn = sqlite_checkpointer(tmp_path / "c.sqlite")
    try:
        run = Investigations(env[2], Agents(*_pair(doc)), saver).start(1, claim, "reviewer@firm")
    finally:
        conn.close()
    assert run["status"] == "awaiting_approval"
    finished = {s.name: s for s in spans.get_finished_spans()}
    assert {"investigation.investigate", "investigation.verify", "investigation.reconcile", "agent", "tool",
            "proposal"} <= set(finished)
    paused = finished["investigation.await_approval"]
    assert paused.attributes["testamur.outcome"] == "paused for a person" and paused.status.is_ok
    assert finished["tool"].attributes["testamur.claim_id"] == claim
    assert finished["investigation.reconcile"].attributes["testamur.run_id"] == run["run"]
