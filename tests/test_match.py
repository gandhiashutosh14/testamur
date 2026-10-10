"""Whose document is this? Identifiers first, names inside the matter, abstain on doubt."""
from datetime import date, datetime

import pytest

from testamur.ids import gstin_with_check, lei_with_check
from testamur.match import match
from testamur.store import Claim, Creditor, Matter, Store, Tenant, UploadRow

LEI_A = lei_with_check("5493001KJTIIGC8Y1R")
GSTIN_A = gstin_with_check(27, "AAKCA1234K")


def _matter(mid: int, name: str) -> Matter:
    return Matter(id=mid, tenant_id=1, name=name, debtor_cin="U64920MH2004PTC123456", commencement=date(2026, 5, 1),
                  announced=date(2026, 5, 3), claims_due=date(2026, 5, 15))


@pytest.fixture()
def store(tmp_path):
    st = Store(f"sqlite:///{tmp_path / 'm.db'}")
    with st.system() as s:
        s.add_all([Tenant(id=1, name="T"), _matter(1, "Pench Valley Polymers Pvt Ltd"),
                   _matter(2, "Malwa Plateau Agro Foods Ltd")])
        s.add_all([
            Creditor(id=1, tenant_id=1, legal_name="Anvaya Industrial Equipment Finance Ltd", lei=LEI_A,
                     gstin=GSTIN_A),
            Creditor(id=2, tenant_id=1, legal_name="Anvaya Industrial Equipment Finance India Ltd"),   # a near twin
            Creditor(id=3, tenant_id=1, legal_name="Kalindi Motor Finance Ltd", trustee_id=4),
            Creditor(id=4, tenant_id=1, legal_name="Dhruvatara Trusteeship Services Ltd"),
            Creditor(id=5, tenant_id=1, legal_name="Ojasvi Credit Pvt Ltd"),
        ])
        s.flush()                                   # claims refer to these creditors: insert them first
        for i, (sid, matter) in enumerate([(1, 1), (2, 1), (3, 1), (5, 2)], start=1):
            s.add(Claim(id=i, tenant_id=1, matter_id=matter, creditor_id=sid, tier="high", claim_type="secured",
                        facility_type="working_capital", book_balance=250_000_000, sanctioned=250_000_000))
    return st


def up(matter=1, declared=None):
    return UploadRow(id=1, tenant_id=1, matter_id=matter, sha256="x", filename="f.pdf", channel="staff_upload",
                     uploaded_at=datetime(2026, 5, 6), declared_creditor_id=declared)


def test_a_valid_identifier_decides_even_against_a_misleading_name(store):
    with store.tenant(1) as s:
        m = match(s, "claim_form", "Someone Else Entirely Ltd", {"gstin": [GSTIN_A]}, up())
        assert (m.creditor_id, m.method) == (1, "identifier")


def test_an_exact_name_beats_its_near_twin(store):
    with store.tenant(1) as s:
        assert match(s, "authority_letter", "Anvaya Industrial Equipment Finance India Ltd", {}, up()).creditor_id == 2
        assert match(s, "authority_letter", "Anvaya Industrial Equipment Finance Limited", {}, up()).creditor_id == 1


def test_a_name_both_twins_could_own_abstains_unless_the_uploader_declares_one(store):
    with store.tenant(1) as s:
        vague = "Anvaya Industrial Equipment Finance Ind Ltd"     # an OCR slip sits between the two names
        assert match(s, "authority_letter", vague, {}, up()).creditor_id is None
        assert match(s, "authority_letter", vague, {}, up(declared=2)).creditor_id == 2


def test_a_name_from_another_matter_is_not_taken(store):
    with store.tenant(1) as s:
        m = match(s, "authority_letter", "Ojasvi Credit Pvt Ltd", {}, up(matter=1))
        assert m.creditor_id is None and "different matter" in m.reason


def test_evidence_for_someone_else_uploaded_by_a_creditor_abstains(store):
    with store.tenant(1) as s:
        m = match(s, "authority_letter", "Kalindi Motor Finance Ltd", {}, up(declared=1))
        assert m.creditor_id is None and set(m.candidates) == {1, 3}


def test_a_trustees_authority_matches_the_trustee_and_resolves_the_holders_it_lists(store):
    with store.tenant(1) as s:
        m = match(s, "authority_letter", "Dhruvatara Trusteeship Services Ltd", {}, up(declared=3),
                  ["Kalindi Motor Finance Ltd"])
        assert (m.creditor_id, m.covers) == (4, [3])


def test_nothing_matches_across_tenants(store):
    with store.system() as s:
        s.add_all([Tenant(id=2, name="Other"),
                   Matter(id=9, tenant_id=2, name="X", commencement=date(2026, 5, 1), announced=date(2026, 5, 3),
                          claims_due=date(2026, 5, 15))])
    with store.tenant(2) as s:
        m = match(s, "claim_form", "Anvaya Industrial Equipment Finance Ltd", {"gstin": [GSTIN_A]},
                  UploadRow(id=2, tenant_id=2, matter_id=9, sha256="y", filename="f.pdf",
                            channel="staff_upload", uploaded_at=datetime(2026, 5, 6)))
        assert m.creditor_id is None
