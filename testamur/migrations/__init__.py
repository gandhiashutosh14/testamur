"""Schema migrations (Alembic). `upgrade(url)` brings a database to the current schema, row-level security included
on Postgres (for the application role `app_role`, by default testamur_app). The service never creates tables itself:
it runs on a database these have brought up to date (testamur db upgrade)."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent


def config(url: str, app_role: str | None = None):
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["app_role"] = app_role
    return cfg


def upgrade(url: str, revision: str = "head", app_role: str | None = None) -> None:
    from alembic import command
    command.upgrade(config(url, app_role), revision)


def downgrade(url: str, revision: str, app_role: str | None = None) -> None:
    from alembic import command
    command.downgrade(config(url, app_role), revision)


def current(url: str) -> str | None:
    """The revision the database is at (None before any)."""
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(config("sqlite://")).get_current_head()
