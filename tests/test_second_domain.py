"""Another domain is another pack: the engine reads a second, unrelated pack (operational creditors' Form B and the
invoices behind it) and judges documents by it, with no change to engine code. What stays domain-specific is listed in
the README: the assistant's question words, the world generator and its ground truth."""
import random
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from testamur.engine import Engine
from testamur.model import Status
from testamur.pack import load_pack_file
from testamur.store import Claim, Creditor, Matter, RequirementState, Store, Tenant
from testamur.synth.render import DocContent, render_pdf

PACK = Path(__file__).parent / "packs" / "operational_claims" / "pack.yaml"
DEBTOR, CREDITOR = "Godavari Packaging Pvt Ltd", "Sahyadri Logistics Pvt Ltd"


def _pdf(lines, seed):
    return render_pdf(DocContent([lines]), random.Random(seed))


def test_the_engine_judges_a_second_domain_from_its_pack_alone(tmp_path):
    pack = load_pack_file(PACK)
    assert pack.name == "operational_claims" and not pack.permissions and not pack.bank_codes
    store = Store(f"sqlite:///{tmp_path / 'op.db'}")
    d = date(2026, 6, 1)
    with store.system() as s:
        s.add_all([Tenant(id=1, name="Firm"),
                   Matter(id=1, tenant_id=1, name=DEBTOR, commencement=d, announced=d, claims_due=date(2026, 6, 15)),
                   Creditor(id=1, tenant_id=1, legal_name=CREDITOR),
                   Claim(id=1, tenant_id=1, matter_id=1, creditor_id=1, tier="standard", claim_type="unsecured",
                         facility_type="trade", book_balance=4_80_000, sanctioned=0)])
    engine = Engine(store, pack, date(2026, 7, 1), tmp_path / "blobs")
    form_b = _pdf([("title", "FORM B"), ("para", "PROOF OF CLAIM BY OPERATIONAL CREDITORS"),
                   ("kv", "In the matter of", DEBTOR), ("kv", "Name of the operational creditor", CREDITOR),
                   ("kv", "Total amount of claim", "Rs. 4,80,000/-")], 1)
    invoice = _pdf([("title", "TAX INVOICE"), ("kv", "Invoice number", "SL/2026/0412"),
                    ("kv", "Issued by", CREDITOR), ("kv", "Billed to", "Konkan Tidewater Marine Pvt Ltd"),
                    ("kv", "Invoice date", "12.03.2026"), ("kv", "Invoice amount", "Rs. 4,80,000/-")], 2)
    at = datetime(2026, 6, 10, 11, 0)
    for name, data in (("form b.pdf", form_b), ("invoice.pdf", invoice)):
        engine.process(1, engine.ingest(1, 1, name, data, "staff_upload", at))
    engine.refresh(1)
    with store.tenant(1) as s:
        statuses = {st.requirement_id: Status(st.status) for st in s.scalars(select(RequirementState))}
    assert statuses == {"proof_of_operational_claim": Status.SATISFIED,
                        "invoices": Status.NOT_SATISFIED}                    # billed to another company
    _, results = engine.verdicts(1, 1, "invoices")
    assert {r.check_id: r.verdict for r in results} == {"invoice_issued_by_creditor": "PASS",
                                                        "invoice_billed_to_debtor": "FAIL"}
