"""The service's configuration, from the environment (Compose sets it; see docker-compose.yml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Settings:
    database_url: str                          # the schema owner: migrations, the job queue, key lookup
    tenant_database_url: str | None = None     # the restricted application role, under row-level security
    blob_root: str = "data/blobs"              # where uploads live, unless TESTAMUR_S3_BUCKET names a bucket
    checkpoints: str = "data/checkpoints.sqlite"   # a Postgres URL, or a SQLite file for one host
    session_secret: str = ""                   # signs reviewers' session cookies
    as_of: date = field(default_factory=date.today)
    model: str | None = None                   # testamur.modeltier.from_spec
    cassette: str | None = None
    secure_cookies: bool = True                # cookies only over HTTPS; off for a local demo over plain HTTP
    max_upload_bytes: int = 20 << 20

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, require_secret: bool = True) -> Settings:
        """`require_secret` is for the API, which signs sessions; a worker or the seed signs nothing."""
        env = os.environ if env is None else env
        secret = env.get("TESTAMUR_SESSION_SECRET", "")
        if require_secret and len(secret) < 32:
            raise RuntimeError("TESTAMUR_SESSION_SECRET must be set, 32 characters or more: it signs review sessions")
        return cls(database_url=env.get("TESTAMUR_DATABASE_URL", "sqlite:///data/service.db"),
                   tenant_database_url=env.get("TESTAMUR_TENANT_DATABASE_URL") or None,
                   blob_root=env.get("TESTAMUR_BLOB_ROOT", "data/blobs"),
                   checkpoints=env.get("TESTAMUR_CHECKPOINTS", "data/checkpoints.sqlite"),
                   session_secret=secret,
                   as_of=date.fromisoformat(env["TESTAMUR_AS_OF"]) if env.get("TESTAMUR_AS_OF") else date.today(),
                   model=env.get("TESTAMUR_MODEL") or None, cassette=env.get("TESTAMUR_CASSETTE") or None,
                   secure_cookies=env.get("TESTAMUR_SECURE_COOKIES", "1") != "0",
                   max_upload_bytes=int(env.get("TESTAMUR_MAX_UPLOAD_BYTES", str(20 << 20))))
