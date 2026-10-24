"""The demo's data: a synthetic world (two firms, their processes, creditors and claims, and the claim documents),
sent through the service's own intake (stored, queued, read by a worker), and keys to sign in with.

Idempotent: the registry and the uploads are added once. Keys are shown once, so each run makes fresh demo keys and
revokes the previous ones: run it again to sign in again."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from .. import agents, jobs
from ..harness import load_registry
from ..store import Tenant, UploadRow, utcnow
from ..synth.world import build_world
from .runtime import Runtime

KEYS = [(1, "demo-reviewer", "reviewer"), (1, "demo-uploader", "uploader"), (2, "demo-reviewer-firm-2", "reviewer")]


def seed(rt: Runtime, out: str | Path, world_seed: int = 3, size: str = "tiny") -> dict:
    world = build_world(seed=world_seed, size=size, out=out, digital_only=True)
    if world.as_of != rt.engine.as_of:
        raise ValueError(f"the world is as of {world.as_of}; set TESTAMUR_AS_OF={world.as_of.isoformat()}")
    with rt.store.system() as s:
        fresh = (s.scalar(select(func.count()).select_from(Tenant)) or 0) == 0
    if fresh:
        load_registry(rt.store, world)
    queued = 0
    for u in world.uploads:
        ref = f"world:{u.id}"
        with rt.store.tenant(u.tenant_id) as s:
            if s.scalar(select(UploadRow.id).where(UploadRow.external_ref == ref)) is not None:
                continue
        uid = rt.engine.ingest(u.tenant_id, u.matter_id, u.filename, (Path(out) / u.file).read_bytes(), u.channel,
                               u.uploaded_at, u.declared_creditor_id, external_ref=ref)
        queued += jobs.enqueue(rt.store, u.tenant_id, "process_upload", f"upload:{uid}", {"upload_id": uid})
    for t in world.tenants:          # statuses that depend on the date alone, once the documents are read
        jobs.enqueue(rt.store, t.id, "refresh", f"refresh:seed:{world_seed}:{len(world.uploads)}",
                     now=utcnow() + timedelta(seconds=30))
    keys = {}
    for tenant_id, name, role in KEYS:
        agents.revoke_key(rt.store, tenant_id, name, by="demo seed")
        keys[name] = {"tenant": tenant_id, "role": role, "key": agents.create_key(rt.store, tenant_id, name, role)}
    return {"registry": "loaded" if fresh else "already there", "uploads_queued": queued,
            "tenants": len(world.tenants), "claims": len(world.claims), "keys": keys}
