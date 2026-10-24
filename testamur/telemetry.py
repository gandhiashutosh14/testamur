"""Traces and structured logs, from upload to outbox.

One OpenTelemetry tracer for the whole engine. Spans carry identifiers and measurements (tenant, matter, claim,
document, run, stage latency, tokens, cache hits, retries, refusal reasons), never document text or secrets: a trace
store is shared infrastructure, and a span is not the place for a creditor's bank account.

Without an exporter configured this is the OpenTelemetry API's no-op tracer and costs nothing. `configure()` installs
an SDK provider: OTLP over HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set (the Compose stack sends to Jaeger), or an
in-memory exporter for tests. Log records are JSON lines with the same identifiers, so a log line and its span are
joined by trace id.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("testamur")
log = logging.getLogger("testamur")

# attributes a span may carry: identifiers and measurements only. Anything else passed is dropped, so a careless call
# site cannot put a field value or a quote into the trace store.
ALLOWED = frozenset({"tenant_id", "matter_id", "claim_id", "document_id", "upload_id", "run_id", "job_id",
                     "proposal_id", "requirement", "check", "role", "tool", "stage", "outcome", "status", "verdict",
                     "tokens", "prompt_tokens", "completion_tokens", "cache_hit", "cached_calls", "retries", "pages",
                     "ocr_pages", "steps", "attempt", "reason", "doc_type", "mode", "kind", "count", "http.route",
                     "ms"})


_PAUSES = frozenset({"GraphInterrupt", "GraphBubbleUp"})


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in attrs.items():
        if k in ALLOWED and v is not None:
            out[f"testamur.{k}"] = v if isinstance(v, bool | int | float) else str(v)[:200]
    return out


@contextmanager
def span(name: str, **attrs):
    """A span for one stage, with its identifiers; yields a setter for measurements known only at the end."""
    t0 = time.perf_counter()
    # exceptions are recorded here, by type only: OpenTelemetry's own recording would copy the message and the
    # traceback, which can quote a document
    with tracer.start_as_current_span(name, attributes=_clean(attrs), record_exception=False,
                                      set_status_on_exception=False) as sp:
        def note(**more):
            for k, v in _clean(more).items():
                sp.set_attribute(k, v)
        try:
            yield note
        except Exception as exc:
            if type(exc).__name__ in _PAUSES:          # a workflow pausing for a person is not an error
                sp.set_attribute("testamur.outcome", "paused for a person")
            else:
                sp.set_attribute("testamur.outcome", f"error: {type(exc).__name__}")
                sp.add_event("exception", {"exception.type": type(exc).__name__})
                sp.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            sp.set_attribute("testamur.ms", round(1000 * (time.perf_counter() - t0), 2))


def traced(name: str, before=None, after=None):
    """Trace a function: `before(*args, **kwargs)` gives the span's identifiers, `after(result)` its measurements."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with span(name, **(before(*args, **kwargs) if before else {})) as note:
                result = fn(*args, **kwargs)
                if after is not None:
                    note(**after(result))
                return result
        return inner
    return wrap


def event(message: str, **attrs) -> None:
    """A structured log line, joined to the current span by its trace id."""
    ctx = trace.get_current_span().get_span_context()
    record = {"msg": message, **{k: v for k, v in attrs.items() if k in ALLOWED}}
    if ctx.is_valid:
        record["trace_id"] = f"{ctx.trace_id:032x}"
    log.info(json.dumps(record, default=str))


def configure(service: str = "testamur", exporter=None) -> None:
    """Install an SDK tracer provider. `exporter` (for tests: an InMemorySpanExporter) or OTLP over HTTP when
    OTEL_EXPORTER_OTLP_ENDPOINT is set; otherwise nothing is installed and spans stay free no-ops."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    if exporter is None and not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    global tracer
    tracer = trace.get_tracer("testamur")
