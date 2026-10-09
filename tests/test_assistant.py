"""The assistant answers from the engine's records, cites only what is on the page, never reaches another tenant,
asks back when a name is ambiguous, and never quotes text aimed at automated readers."""
from sqlalchemy import select

from testamur.assistant import Assistant
from testamur.golden import build, evaluate
from testamur.store import Document


def test_the_golden_set_on_the_tiny_world(env):
    world, store, engine = env
    r = evaluate(world, store, engine, build(world))
    wrong = [x for x in r["rows"] if not x["right"]]
    assert not wrong, wrong[:3]
    fields = r["by_type"]["field"]
    assert fields["wrong_value"] == fields["wrong_version"] == fields["wrong_entity"] == 0
    assert all(c.get("leaked", 0) == 0 for c in r["by_type"].values())
    assert {"field", "near_twin", "another_tenant", "unanswerable", "expiring"} <= set(r["by_type"])


def test_a_quote_that_is_not_on_the_page_is_not_cited(env):
    world, store, engine = env
    a = Assistant(engine)
    with store.tenant(1) as s:
        doc = s.scalars(select(Document).where(Document.doc_type == "insurance_certificate")).first()
        line = doc.fields["expiry_date"]["line"]
        assert a._verified(s, doc, doc.fields["expiry_date"]["page"], line) is not None
        assert a._verified(s, doc, doc.fields["expiry_date"]["page"], "Expiry date: 31/12/2099 (renewed)") is None


def test_another_tenants_supplier_is_never_found(env):
    world, store, engine = env
    mine = {x.legal_name for x in world.suppliers if x.tenant_id == 1}
    other = next(x for x in world.suppliers if x.tenant_id == 2 and x.legal_name not in mine)
    a = Assistant(engine).ask(1, f"When does {other.legal_name}'s insurance expire?")
    assert a.kind in ("refuse", "clarify") and not a.citations
    assert all(name in mine for name in a.candidates)


def test_a_shared_first_word_is_asked_back(env):
    world, store, engine = env
    q = next(q for q in build(world) if q["type"] == "ambiguous")
    a = Assistant(engine).ask(q["tenant"], q["question"])
    assert a.kind == "clarify" and len(a.candidates) >= 2


def test_text_aimed_at_automated_readers_is_never_quoted(env):
    world, store, engine = env
    a = Assistant(engine)
    with store.tenant(1) as s:
        doc = s.scalars(select(Document).where(Document.hostile.is_(True))).first()
        planted = next(line.text for p in a._extraction(s, doc).pages for line in p.lines
                       if any(h.search(line.text) for h in engine.pack.injection_patterns()))
        hits = a._retrieve(s, [doc], planted, k=5)
    assert all(not any(h.search(c.quote) for h in engine.pack.injection_patterns()) for c in hits)
