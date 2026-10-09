"""Whose document is this? Identifiers first, names inside the business unit, abstain on doubt."""
from datetime import date, datetime

import pytest

from testamur.ids import iban_with_check, lei_with_check
from testamur.match import match
from testamur.store import BusinessUnit, Engagement, Store, Supplier, Tenant, UploadRow

LEI_A = lei_with_check("5493001KJTIIGC8Y1R")
IBAN_A = iban_with_check("GB", "NMBK12345678901234")


@pytest.fixture()
def store(tmp_path):
    st = Store(f"sqlite:///{tmp_path / 'm.db'}")
    with st.system() as s:
        s.add_all([Tenant(id=1, name="T"), BusinessUnit(id=1, tenant_id=1, name="North"),
                   BusinessUnit(id=2, tenant_id=1, name="South")])
        s.add_all([
            Supplier(id=1, tenant_id=1, legal_name="Brightwater Payroll Services Ltd", lei=LEI_A, iban=IBAN_A),
            Supplier(id=2, tenant_id=1, legal_name="Brightwater Payroll Services UK Ltd"),       # a near twin
            Supplier(id=3, tenant_id=1, legal_name="Kestrel Freight Ltd", parent_id=4),
            Supplier(id=4, tenant_id=1, legal_name="Kestrel Holdings plc"),
            Supplier(id=5, tenant_id=1, legal_name="Oakhurst Cleaning Ltd"),
        ])
        for i, (sid, bu) in enumerate([(1, 1), (2, 1), (3, 1), (5, 2)], start=1):
            s.add(Engagement(id=i, tenant_id=1, business_unit_id=bu, supplier_id=sid, tier="high",
                             engagement_type="services", service_category="facilities",
                             start_date=date(2026, 1, 1), end_date=date(2027, 1, 1)))
    return st


def up(bu=1, claimed=None):
    return UploadRow(id=1, tenant_id=1, business_unit_id=bu, sha256="x", filename="f.pdf", channel="staff_upload",
                     uploaded_at=datetime(2026, 1, 1), claimed_supplier_id=claimed)


def test_a_valid_identifier_decides_even_against_a_misleading_name(store):
    with store.tenant(1) as s:
        m = match(s, "tax_registration", "Someone Else Entirely Ltd", {"lei": [LEI_A]}, up())
        assert (m.supplier_id, m.method) == (1, "identifier")


def test_an_exact_name_beats_its_near_twin(store):
    with store.tenant(1) as s:
        assert match(s, "insurance_certificate", "Brightwater Payroll Services UK Ltd", {}, up()).supplier_id == 2
        assert match(s, "insurance_certificate", "Brightwater Payroll Services Limited", {}, up()).supplier_id == 1


def test_a_name_both_twins_could_own_abstains_unless_the_uploader_claims_one(store):
    with store.tenant(1) as s:
        vague = "Brightwater Payroll Service UK Ltd"            # a typo sits between the two names
        assert match(s, "insurance_certificate", vague, {}, up()).supplier_id is None
        assert match(s, "insurance_certificate", vague, {}, up(claimed=2)).supplier_id == 2


def test_a_name_from_another_business_unit_is_not_taken(store):
    with store.tenant(1) as s:
        m = match(s, "insurance_certificate", "Oakhurst Cleaning Ltd", {}, up(bu=1))
        assert m.supplier_id is None and "different business unit" in m.reason


def test_evidence_for_someone_else_uploaded_by_a_supplier_abstains(store):
    with store.tenant(1) as s:
        m = match(s, "insurance_certificate", "Kestrel Freight Ltd", {}, up(claimed=1))
        assert m.supplier_id is None and set(m.candidates) == {1, 3}


def test_a_group_policy_matches_the_parent_and_resolves_covered_subsidiaries(store):
    with store.tenant(1) as s:
        m = match(s, "insurance_certificate", "Kestrel Holdings plc", {}, up(claimed=3), ["Kestrel Freight Ltd"])
        assert (m.supplier_id, m.covers) == (4, [3])


def test_nothing_matches_across_tenants(store):
    with store.system() as s:
        s.add_all([Tenant(id=2, name="Other"), BusinessUnit(id=9, tenant_id=2, name="X")])
    with store.tenant(2) as s:
        m = match(s, "tax_registration", "Brightwater Payroll Services Ltd", {"lei": [LEI_A]},
                  UploadRow(id=2, tenant_id=2, business_unit_id=9, sha256="y", filename="f.pdf",
                            channel="staff_upload", uploaded_at=datetime(2026, 1, 1)))
        assert m.supplier_id is None
