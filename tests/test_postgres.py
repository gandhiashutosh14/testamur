"""The engine on Postgres gives exactly the statuses it gives on SQLite. Runs in the postgres CI job, where
TESTAMUR_PG_URL points at a real server; elsewhere it is skipped with an allow-listed reason."""
import os

import pytest
from sqlalchemy import select

from testamur.engine import Engine
from testamur.harness import load_registry
from testamur.pack import load_pack
from testamur.store import RequirementState, Store, enable_row_level_security
from testamur.synth.world import build_world

pytestmark = pytest.mark.skipif(not os.environ.get("TESTAMUR_PG_URL"),
                                reason="needs a Postgres server: runs in the postgres CI job")


def _statuses(url, world, out, blobs):
    store = Store(url)
    load_registry(store, world)
    engine = Engine(store, load_pack(), world.as_of, blobs)
    for u in world.uploads:
        engine.process(u.tenant_id, engine.ingest(u.tenant_id, u.matter_id, u.filename,
                                                  (out / u.file).read_bytes(), u.channel, u.uploaded_at,
                                                  u.declared_creditor_id))
    for t in world.tenants:
        engine.refresh(t.id)
    with store.system() as s:
        result = {(r.tenant_id, r.claim_id, r.requirement_id): r.status for r in s.scalars(select(RequirementState))}
    store.engine.dispose()
    return result


def test_the_engine_gives_the_same_statuses_on_postgres(tmp_path, db_url):
    out = tmp_path / "world"
    world = build_world(seed=3, size="tiny", out=out, digital_only=True)
    on_postgres = _statuses(db_url, world, out, tmp_path / "blobs_pg")
    on_sqlite = _statuses(f"sqlite:///{tmp_path / 'lite.db'}", world, out, tmp_path / "blobs_lite")
    assert len(on_postgres) > 50 and on_postgres == on_sqlite


def test_two_people_confirming_one_proposal_at_once_apply_it_once_on_postgres(tmp_path, db_url):
    """On Postgres the proposal row is locked FOR UPDATE inside the tenant's transaction: of two confirmations racing,
    one applies it and the other finds it applied. The effect exists once."""
    import threading

    from sqlalchemy import func

    from testamur import agents
    from testamur.store import CheckReview
    out = tmp_path / "world"
    world = build_world(seed=3, size="tiny", out=out, digital_only=True)
    store = Store(db_url)
    load_registry(store, world)
    engine = Engine(store, load_pack(), world.as_of, tmp_path / "blobs")
    for u in world.uploads:
        engine.process(u.tenant_id, engine.ingest(u.tenant_id, u.matter_id, u.filename, (out / u.file).read_bytes(),
                                                  u.channel, u.uploaded_at, u.declared_creditor_id))
    engine.refresh(1)
    with store.tenant(1) as s:
        claim = s.scalar(select(RequirementState.claim_id).where(RequirementState.requirement_id == "authority",
                                                                 RequirementState.status == "SATISFIED"))
    p = agents.propose(engine, 1, "agent-a", "check_verdict", {"claim": claim, "requirement": "authority",
                                                              "check": "auth_creditor_matches", "verdict": "FAIL"},
                       "not the creditor's signatory")
    outcomes, errors, start = [], [], threading.Barrier(2)

    def confirm(who):
        start.wait()
        try:
            outcomes.append(agents.decide(engine, 1, p["proposal"], who, confirm=True))
        except ValueError as exc:
            errors.append(exc)
    threads = [threading.Thread(target=confirm, args=(f"reviewer-{i}@firm",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(outcomes) == 1 and len(errors) == 1
    with store.tenant(1) as s:
        assert s.scalar(select(func.count()).select_from(CheckReview).where(
            CheckReview.proposal_id == p["proposal"])) == 1
    store.engine.dispose()


APP_ROLE = "testamur_app"


def _app_store(db_url: str) -> Store:
    """The schema owned by the CI superuser; tenant sessions connected as a restricted login role (not the owner, no
    BYPASSRLS), with row-level security installed."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    owner = create_engine(db_url)
    with owner.begin() as conn:
        exists = conn.exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}'").first()
        if not exists:
            conn.exec_driver_sql(f"CREATE ROLE {APP_ROLE} LOGIN NOBYPASSRLS NOSUPERUSER")
    owner.dispose()
    store = Store(db_url, tenant_url=make_url(db_url).set(username=APP_ROLE).render_as_string(hide_password=False))
    enable_row_level_security(store, APP_ROLE)
    return store


def test_row_level_security_refuses_other_tenants_rows_to_any_statement(db_url):
    """Defence in depth: a statement that bypasses the session's filter (here, textual SQL the session was told to
    trust) still sees, changes and creates only the session tenant's rows, because the database refuses the rest."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from testamur.store import TENANT_SAFE, Creditor, Tenant
    store = _app_store(db_url)
    with store.system() as s:
        s.add_all([Tenant(id=1, name="A"), Tenant(id=2, name="B"),
                   Creditor(id=1, tenant_id=1, legal_name="Alpha Ltd"), Creditor(id=2, tenant_id=2, legal_name="Beta Ltd")])
    trusted = {TENANT_SAFE: True}
    with store.tenant(1) as s:
        assert s.execute(text("SELECT count(*) FROM creditors").execution_options(**trusted)).scalar() == 1
        assert s.execute(text("UPDATE creditors SET legal_name = 'Taken Ltd'").execution_options(**trusted)).rowcount == 1
        assert s.execute(text("DELETE FROM creditors WHERE id = 2").execution_options(**trusted)).rowcount == 0
    with pytest.raises(DBAPIError), store.tenant(1) as s:
        s.execute(text("INSERT INTO creditors (id, tenant_id, legal_name) VALUES (3, 2, 'Planted Ltd')")
                  .execution_options(**trusted))
    with store.tenant_engine.connect() as conn:          # no tenant set at all: nothing is visible
        assert conn.exec_driver_sql("SELECT count(*) FROM creditors").scalar() == 0
    with store.system() as s:
        assert s.get(Creditor, 2).legal_name == "Beta Ltd" and s.get(Creditor, 3) is None
    store.engine.dispose()
    store.tenant_engine.dispose()
