"""The v0.5 schema: every table of testamur.store and testamur.jobs, generated from the models.

Revision ID: 0001
Revises:
Create Date: 2026-10-25 00:09:29.411706
"""
import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('available_at', sa.DateTime(), nullable=False),
    sa.Column('lease_until', sa.DateTime(), nullable=True),
    sa.Column('leased_by', sa.String(length=80), nullable=True),
    sa.Column('lease_token', sa.String(length=40), nullable=True),
    sa.Column('deferrals', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'key')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_available_at'), ['available_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_jobs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('settings',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('tenants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('api_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_keys_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('audit',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('actor', sa.String(length=300), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('blobs',
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('mime', sa.String(length=60), nullable=False),
    sa.Column('path', sa.String(length=400), nullable=False),
    sa.Column('first_seen', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('sha256', 'tenant_id')
    )
    op.create_table('budgets',
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('ocr_pages_per_day', sa.Integer(), nullable=True),
    sa.Column('model_tokens_per_day', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('tenant_id')
    )
    op.create_table('check_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('check_id', sa.String(length=60), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('after_review_id', sa.Integer(), nullable=False),
    sa.Column('reviewer', sa.String(length=300), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('check_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_check_events_check_id'), ['check_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_check_events_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('creditors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legal_name', sa.String(length=300), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=False),
    sa.Column('cin', sa.String(length=40), nullable=True),
    sa.Column('gstin', sa.String(length=40), nullable=True),
    sa.Column('lei', sa.String(length=40), nullable=True),
    sa.Column('account_number', sa.String(length=40), nullable=True),
    sa.Column('ifsc', sa.String(length=20), nullable=True),
    sa.Column('trustee_id', sa.Integer(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'trustee_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_creditors_tenant_id')
    )
    with op.batch_alter_table('creditors', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_creditors_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('extractions',
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('extractor', sa.String(length=60), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('ocr_pages', sa.Integer(), nullable=False),
    sa.Column('seconds', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('sha256', 'tenant_id', 'extractor', 'version')
    )
    op.create_table('matters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('debtor_cin', sa.String(length=40), nullable=False),
    sa.Column('commencement', sa.Date(), nullable=False),
    sa.Column('announced', sa.Date(), nullable=False),
    sa.Column('claims_due', sa.Date(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_matters_tenant_id')
    )
    with op.batch_alter_table('matters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_matters_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('model_calls',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('fingerprint', sa.String(length=160), nullable=False),
    sa.Column('response', sa.Text(), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('seconds', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('key', 'tenant_id')
    )
    op.create_table('outbox',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('dispatched_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'key')
    )
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outbox_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('proposals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('origin', sa.String(length=160), nullable=True),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('args', sa.JSON(), nullable=False),
    sa.Column('binding', sa.JSON(), nullable=False),
    sa.Column('binding_digest', sa.String(length=16), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('proposed_by', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('decided_by', sa.String(length=300), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('outcome', sa.Text(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'origin')
    )
    with op.batch_alter_table('proposals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_proposals_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('reading_locks',
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('owner', sa.String(length=40), nullable=False),
    sa.Column('locked_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('sha256', 'tenant_id')
    )
    op.create_table('tool_grants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tool', sa.String(length=60), nullable=False),
    sa.Column('principal', sa.String(length=80), nullable=False),
    sa.Column('granted_by', sa.String(length=300), nullable=False),
    sa.Column('granted_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'tool', 'principal')
    )
    with op.batch_alter_table('tool_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tool_grants_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('units', sa.Float(), nullable=False),
    sa.Column('cached', sa.Boolean(), nullable=False),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('reserved_by', sa.String(length=40), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_usage_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('webhook_endpoints',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=400), nullable=False),
    sa.Column('secret', sa.String(length=120), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_webhook_endpoints_tenant_id')
    )
    with op.batch_alter_table('webhook_endpoints', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_endpoints_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('claims',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('matter_id', sa.Integer(), nullable=False),
    sa.Column('creditor_id', sa.Integer(), nullable=False),
    sa.Column('tier', sa.String(length=20), nullable=False),
    sa.Column('claim_type', sa.String(length=40), nullable=False),
    sa.Column('facility_type', sa.String(length=60), nullable=False),
    sa.Column('book_balance', sa.BigInteger(), nullable=False),
    sa.Column('sanctioned', sa.BigInteger(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'creditor_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'matter_id'], ['matters.tenant_id', 'matters.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_claims_tenant_id')
    )
    with op.batch_alter_table('claims', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_claims_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('deliveries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('endpoint_id', sa.Integer(), nullable=False),
    sa.Column('webhook_id', sa.String(length=160), nullable=False),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'endpoint_id'], ['webhook_endpoints.tenant_id', 'webhook_endpoints.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'endpoint_id', 'webhook_id')
    )
    with op.batch_alter_table('deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_deliveries_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('matter_id', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('doc_type', sa.String(length=60), nullable=True),
    sa.Column('class_method', sa.String(length=40), nullable=False),
    sa.Column('class_score', sa.Float(), nullable=False),
    sa.Column('creditor_id', sa.Integer(), nullable=True),
    sa.Column('covers', sa.JSON(), nullable=False),
    sa.Column('match_method', sa.String(length=40), nullable=False),
    sa.Column('match_score', sa.Float(), nullable=False),
    sa.Column('match_detail', sa.JSON(), nullable=False),
    sa.Column('fields', sa.JSON(), nullable=False),
    sa.Column('conflicts', sa.JSON(), nullable=False),
    sa.Column('identifiers', sa.JSON(), nullable=False),
    sa.Column('hostile', sa.Boolean(), nullable=False),
    sa.Column('cleared', sa.Boolean(), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('engine_version', sa.String(length=80), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'creditor_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'matter_id'], ['matters.tenant_id', 'matters.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_documents_tenant_id'),
    sa.UniqueConstraint('tenant_id', 'matter_id', 'sha256')
    )
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_documents_matter_id'), ['matter_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_documents_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('uploads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('matter_id', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('channel', sa.String(length=40), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(), nullable=False),
    sa.Column('declared_creditor_id', sa.Integer(), nullable=True),
    sa.Column('external_ref', sa.String(length=80), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['sha256', 'tenant_id'], ['blobs.sha256', 'blobs.tenant_id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'declared_creditor_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'matter_id'], ['matters.tenant_id', 'matters.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_uploads_sha256'), ['sha256'], unique=False)
        batch_op.create_index(batch_op.f('ix_uploads_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('attestations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('creditor_id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=True),
    sa.Column('requirement_id', sa.String(length=60), nullable=False),
    sa.Column('verified_by', sa.String(length=120), nullable=False),
    sa.Column('verified_on', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'creditor_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('attestations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attestations_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('check_reviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.String(length=60), nullable=False),
    sa.Column('check_id', sa.String(length=60), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('verdict', sa.String(length=10), nullable=False),
    sa.Column('machine_verdict', sa.String(length=10), nullable=False),
    sa.Column('rule_digest', sa.String(length=16), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('reviewer', sa.String(length=300), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('proposal_id', sa.Integer(), nullable=True),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'document_id'], ['documents.tenant_id', 'documents.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('proposal_id')
    )
    with op.batch_alter_table('check_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_check_reviews_check_id'), ['check_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_check_reviews_claim_id'), ['claim_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_check_reviews_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_check_reviews_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('claim_permissions',
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('granted', sa.Boolean(), nullable=False),
    sa.Column('unmet', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('claim_id', 'action', 'tenant_id')
    )
    op.create_table('document_reviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=10), nullable=False),
    sa.Column('doc_type', sa.String(length=60), nullable=True),
    sa.Column('creditor_id', sa.Integer(), nullable=True),
    sa.Column('machine', sa.JSON(), nullable=False),
    sa.Column('reviewer', sa.String(length=300), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('proposal_id', sa.Integer(), nullable=True),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'creditor_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'document_id'], ['documents.tenant_id', 'documents.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('proposal_id')
    )
    with op.batch_alter_table('document_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_reviews_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_reviews_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('field_corrections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(length=60), nullable=False),
    sa.Column('raw', sa.Text(), nullable=False),
    sa.Column('machine_raw', sa.Text(), nullable=True),
    sa.Column('reviewer', sa.String(length=300), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('proposal_id', sa.Integer(), nullable=True),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'document_id'], ['documents.tenant_id', 'documents.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('proposal_id')
    )
    with op.batch_alter_table('field_corrections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_field_corrections_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_field_corrections_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('holds',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('placed_at', sa.DateTime(), nullable=False),
    sa.Column('released_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('holds', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_holds_claim_id'), ['claim_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_holds_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('one_open_hold', ['tenant_id', 'claim_id', 'action'], unique=True, sqlite_where=sa.text('released_at IS NULL'), postgresql_where=sa.text('released_at IS NULL'))

    op.create_table('investigation_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.String(length=40), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('requested_by', sa.String(length=300), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('packet', sa.JSON(), nullable=True),
    sa.Column('report', sa.JSON(), nullable=True),
    sa.Column('recommendations', sa.JSON(), nullable=False),
    sa.Column('stats', sa.JSON(), nullable=False),
    sa.Column('outcomes', sa.JSON(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('tokens', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'id', name='uq_investigation_runs_tenant_id'),
    sa.UniqueConstraint('thread_id')
    )
    with op.batch_alter_table('investigation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_investigation_runs_claim_id'), ['claim_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_investigation_runs_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=160), nullable=False),
    sa.Column('creditor_id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.String(length=60), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'creditor_id'], ['creditors.tenant_id', 'creditors.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'key')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_claim_id'), ['claim_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('requirement_states',
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.String(length=60), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=True),
    sa.Column('via_trustee', sa.Boolean(), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('failing', sa.JSON(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('evidence_digest', sa.String(length=16), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'document_id'], ['documents.tenant_id', 'documents.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('claim_id', 'requirement_id', 'tenant_id')
    )
    op.create_table('tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.String(length=60), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('opened_at', sa.DateTime(), nullable=False),
    sa.Column('due_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('closed_reason', sa.Text(), nullable=False),
    sa.Column('event_key', sa.String(length=120), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tasks_claim_id'), ['claim_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_tasks_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('one_open_task', ['tenant_id', 'claim_id', 'requirement_id'], unique=True, sqlite_where=sa.text("status = 'open'"), postgresql_where=sa.text("status = 'open'"))

    op.create_table('verdicts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('requirement_id', sa.String(length=60), nullable=False),
    sa.Column('check_id', sa.String(length=60), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('verdict', sa.String(length=10), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('tier', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('fix_hint', sa.Text(), nullable=True),
    sa.Column('transient', sa.Boolean(), nullable=False),
    sa.Column('lapsed', sa.Boolean(), nullable=False),
    sa.Column('engine_version', sa.String(length=80), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'claim_id'], ['claims.tenant_id', 'claims.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id', 'document_id'], ['documents.tenant_id', 'documents.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'claim_id', 'requirement_id', 'check_id', 'document_id', 'engine_version')
    )
    with op.batch_alter_table('verdicts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_verdicts_claim_id'), ['claim_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_verdicts_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_verdicts_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('agent_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('step', sa.Integer(), nullable=False),
    sa.Column('tool', sa.String(length=40), nullable=False),
    sa.Column('args', sa.JSON(), nullable=False),
    sa.Column('outcome', sa.String(length=40), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('ms', sa.Float(), nullable=False),
    sa.Column('at', sa.DateTime(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'run_id'], ['investigation_runs.tenant_id', 'investigation_runs.id'], initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], initially='DEFERRED', deferrable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_events_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_events_tenant_id'), ['tenant_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('agent_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_events_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_agent_events_run_id'))

    op.drop_table('agent_events')
    with op.batch_alter_table('verdicts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_verdicts_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_verdicts_document_id'))
        batch_op.drop_index(batch_op.f('ix_verdicts_claim_id'))

    op.drop_table('verdicts')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index('one_open_task', sqlite_where=sa.text("status = 'open'"), postgresql_where=sa.text("status = 'open'"))
        batch_op.drop_index(batch_op.f('ix_tasks_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_tasks_claim_id'))

    op.drop_table('tasks')
    op.drop_table('requirement_states')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_claim_id'))

    op.drop_table('notifications')
    with op.batch_alter_table('investigation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_investigation_runs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_investigation_runs_claim_id'))

    op.drop_table('investigation_runs')
    with op.batch_alter_table('holds', schema=None) as batch_op:
        batch_op.drop_index('one_open_hold', sqlite_where=sa.text('released_at IS NULL'), postgresql_where=sa.text('released_at IS NULL'))
        batch_op.drop_index(batch_op.f('ix_holds_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_holds_claim_id'))

    op.drop_table('holds')
    with op.batch_alter_table('field_corrections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_field_corrections_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_field_corrections_document_id'))

    op.drop_table('field_corrections')
    with op.batch_alter_table('document_reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_reviews_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_document_reviews_document_id'))

    op.drop_table('document_reviews')
    op.drop_table('claim_permissions')
    with op.batch_alter_table('check_reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_check_reviews_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_check_reviews_document_id'))
        batch_op.drop_index(batch_op.f('ix_check_reviews_claim_id'))
        batch_op.drop_index(batch_op.f('ix_check_reviews_check_id'))

    op.drop_table('check_reviews')
    with op.batch_alter_table('attestations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attestations_tenant_id'))

    op.drop_table('attestations')
    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_uploads_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_uploads_sha256'))

    op.drop_table('uploads')
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_documents_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_documents_matter_id'))

    op.drop_table('documents')
    with op.batch_alter_table('deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_deliveries_tenant_id'))

    op.drop_table('deliveries')
    with op.batch_alter_table('claims', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_claims_tenant_id'))

    op.drop_table('claims')
    with op.batch_alter_table('webhook_endpoints', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_endpoints_tenant_id'))

    op.drop_table('webhook_endpoints')
    with op.batch_alter_table('usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_usage_tenant_id'))

    op.drop_table('usage')
    with op.batch_alter_table('tool_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tool_grants_tenant_id'))

    op.drop_table('tool_grants')
    op.drop_table('reading_locks')
    with op.batch_alter_table('proposals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_proposals_tenant_id'))

    op.drop_table('proposals')
    with op.batch_alter_table('outbox', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outbox_tenant_id'))

    op.drop_table('outbox')
    op.drop_table('model_calls')
    with op.batch_alter_table('matters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_matters_tenant_id'))

    op.drop_table('matters')
    op.drop_table('extractions')
    with op.batch_alter_table('creditors', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_creditors_tenant_id'))

    op.drop_table('creditors')
    with op.batch_alter_table('check_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_check_events_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_check_events_check_id'))

    op.drop_table('check_events')
    op.drop_table('budgets')
    op.drop_table('blobs')
    with op.batch_alter_table('audit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_tenant_id'))

    op.drop_table('audit')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_tenant_id'))

    op.drop_table('api_keys')
    op.drop_table('tenants')
    op.drop_table('settings')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_jobs_available_at'))

    op.drop_table('jobs')
