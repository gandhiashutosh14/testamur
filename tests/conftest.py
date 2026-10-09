"""Suite-wide rules: a skipped test is not a passed test, and a suite that shrank is not a passing suite.

Any skip not on the allow-list below (with its reason) fails the run, so a test that silently stopped running cannot
leave the build green. A full run that collects fewer tests than the floor fails too, so a deleted test file or one
that no longer collects cannot either. Raise the floor when tests are added; lowering it needs a reason in the commit
message."""
from __future__ import annotations

import os
import shutil

import pytest

from testamur.engine import Engine
from testamur.harness import load_registry
from testamur.pack import load_pack
from testamur.store import Store
from testamur.synth.world import build_world

PG_URL = os.environ.get("TESTAMUR_PG_URL")       # set by the postgres CI job
ALLOWED_SKIPS: dict[str, str] = {
    **({} if PG_URL else {
        "tests/test_postgres.py::test_the_engine_gives_the_same_statuses_on_postgres":
            "needs a Postgres server: runs in the postgres CI job",
    }),
}
MIN_TESTS = 142


def pytest_collection_finish(session):
    whole_suite = session.config.args_source == pytest.Config.ArgsSource.TESTPATHS and not (
        session.config.option.keyword or session.config.option.markexpr)
    session.config.stash[_SHRANK] = len(session.items) < MIN_TESTS if whole_suite else False


_SHRANK = pytest.StashKey[bool]()


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    if session.config.stash.get(_SHRANK, False):
        reporter.write_line(f"FAILED: the suite collected fewer than {MIN_TESTS} tests: tests were lost, or the floor "
                            "was not raised", red=True)
        session.exitstatus = 1
    unexpected = [r.nodeid for r in reporter.stats.get("skipped", []) if r.nodeid not in ALLOWED_SKIPS]
    if unexpected:
        reporter.write_line(f"FAILED: {len(unexpected)} test(s) skipped without an allow-listed reason:", red=True)
        for nodeid in unexpected:
            reporter.write_line(f"  {nodeid}", red=True)
        session.exitstatus = 1


# ---- a processed tiny world, shared by the tests of people's decisions and of accuracy

@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("world")
    world = build_world(seed=3, size="tiny", out=out, digital_only=True)
    base = tmp_path_factory.mktemp("base")
    store = Store(f"sqlite:///{base / 'w.db'}")
    load_registry(store, world)
    engine = Engine(store, load_pack(), world.as_of, base / "blobs")
    for u in world.uploads:
        uid = engine.ingest(u.tenant_id, u.business_unit_id, u.filename, (out / u.file).read_bytes(), u.channel,
                            u.uploaded_at, u.claimed_supplier_id, external_ref=f"world:{u.id}")
        engine.process(u.tenant_id, uid)
    for t in world.tenants:
        engine.refresh(t.id)
    store.engine.dispose()
    return world, base


@pytest.fixture
def env(built, tmp_path):
    """A private copy of the processed tiny world for each test, since decisions change it."""
    world, base = built
    for name in ("w.db", "w.db-wal", "w.db-shm"):
        if (base / name).exists():
            shutil.copy(base / name, tmp_path / name)
    shutil.copytree(base / "blobs", tmp_path / "blobs")
    store = Store(f"sqlite:///{tmp_path / 'w.db'}")
    yield world, store, Engine(store, load_pack(), world.as_of, tmp_path / "blobs")
    store.engine.dispose()


@pytest.fixture
def db_url(tmp_path):
    """A fresh database for one test: SQLite in a temporary folder, or, when TESTAMUR_PG_URL is set, a clean Postgres
    schema, so the same tests run on both."""
    if not PG_URL:
        return f"sqlite:///{tmp_path / 'test.db'}"
    from sqlalchemy import create_engine

    from testamur.store import Base
    engine = create_engine(PG_URL)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return PG_URL
