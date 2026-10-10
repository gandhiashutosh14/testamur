"""The engine on Postgres gives exactly the statuses it gives on SQLite. Runs in the postgres CI job, where
TESTAMUR_PG_URL points at a real server; elsewhere it is skipped with an allow-listed reason."""
import os

import pytest
from sqlalchemy import select

from testamur.engine import Engine
from testamur.harness import load_registry
from testamur.pack import load_pack
from testamur.store import RequirementState, Store
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
