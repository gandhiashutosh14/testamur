"""Row-level security on Postgres: every tenant table admits only the rows of the tenant a transaction set, to the
application role (testamur.store.rls_statements). A no-op on SQLite. The tables are named here, not read from the
models, so that this revision means the same thing after later ones add tables: each of those covers its own.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 00:10:00
"""
import os
import re

from alembic import op

from testamur.store import rls_drop_statements, rls_statements

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

RLS_TABLES = [
    "tenants", "api_keys", "audit", "blobs", "budgets", "check_events", "creditors", "extractions", "matters",
    "model_calls", "outbox", "proposals", "reading_locks", "tool_grants", "usage", "webhook_endpoints", "claims",
    "deliveries", "documents", "uploads", "attestations", "check_reviews", "claim_permissions", "document_reviews",
    "field_corrections", "holds", "investigation_runs", "notifications", "requirement_states", "tasks", "verdicts",
    "agent_events",
]


def _role() -> str:
    role = op.get_context().config.attributes.get("app_role") or os.environ.get("TESTAMUR_APP_ROLE", "testamur_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"not a plain role name: {role!r}")
    return role


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role = _role()
    # the role is a cluster object: create it (without a login) only if the deployment has not; a deployment that
    # connects as it gives it LOGIN and a password itself, outside the schema's history
    bind.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                         f"CREATE ROLE {bind.dialect.identifier_preparer.quote(role)} NOLOGIN NOBYPASSRLS; "
                         f"END IF; END $$")
    for sql in rls_statements(bind.dialect.identifier_preparer, role, RLS_TABLES):
        bind.exec_driver_sql(sql)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for sql in rls_drop_statements(bind.dialect.identifier_preparer, _role(), RLS_TABLES):
        bind.exec_driver_sql(sql)
