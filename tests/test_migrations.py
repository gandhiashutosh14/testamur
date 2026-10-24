"""The schema's history: migrations bring an empty database to exactly the models (nothing missing, nothing extra),
go back down cleanly, and on Postgres install row-level security on every tenant table, so a deployment that never
calls create_all is as isolated as the tests' databases are."""
import importlib.util
import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from testamur import (
    jobs,  # noqa: F401  (the jobs table is part of the schema)
    migrations,
)
from testamur.store import Base, Store, Tenant, tenant_tables

PG_URL = os.environ.get("TESTAMUR_PG_URL")


def _diffs(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()


def test_the_migrations_build_exactly_the_models_and_come_back_down(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrations.upgrade(url)
    assert migrations.current(url) == migrations.head()
    assert _diffs(url) == []
    migrations.downgrade(url, "base")
    engine = create_engine(url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
    migrations.upgrade(url)
    assert _diffs(url) == []


def test_a_store_runs_on_a_migrated_database_without_creating_anything(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    migrations.upgrade(url)
    store = Store(url, create_schema=False)
    with store.system() as s:
        s.add(Tenant(id=1, name="a firm"))
    with store.tenant(1) as s:
        assert s.get(Tenant, 1).name == "a firm"
    store.engine.dispose()


def test_every_tenant_table_is_under_row_level_security_in_some_migration():
    covered = set()
    for path in sorted((migrations.HERE / "versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        covered |= set(getattr(module, "RLS_TABLES", []))
    assert {"tenants", *tenant_tables()} <= covered


@pytest.mark.skipif(not PG_URL, reason="needs a Postgres server: runs in the postgres CI job")
def test_on_postgres_the_migrations_alone_isolate_tenants():
    owner = create_engine(PG_URL)
    with owner.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        if conn.exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname = 'testamur_app'").first() is None:
            conn.exec_driver_sql("CREATE ROLE testamur_app LOGIN NOBYPASSRLS NOSUPERUSER")
        else:
            conn.exec_driver_sql("ALTER ROLE testamur_app LOGIN")
    migrations.upgrade(PG_URL, app_role="testamur_app")
    assert _diffs(PG_URL) == []
    with owner.connect() as conn:
        policies = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_policies "
                                                    "WHERE policyname = 'tenant_isolation'"))}
    assert {"tenants", *tenant_tables()} <= policies
    app_url = make_url(PG_URL).set(username="testamur_app").render_as_string(hide_password=False)
    store = Store(PG_URL, tenant_url=app_url, create_schema=False)
    with store.system() as s:
        s.add_all([Tenant(id=1, name="one"), Tenant(id=2, name="two")])
    with store.tenant(1) as s:
        rows = s.execute(text("SELECT id FROM tenants").execution_options(tenant_safe=True)).all()
        assert [r[0] for r in rows] == [1]                 # the database itself hides the other firm
    store.engine.dispose()
    store.tenant_engine.dispose()
    owner.dispose()
