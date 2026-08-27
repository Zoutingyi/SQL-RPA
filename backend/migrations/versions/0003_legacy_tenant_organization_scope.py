"""Map legacy tenants and backfill organization scope.

Revision ID: 0003_legacy_tenant_organization_scope
Revises: 0002_four_level_organizations
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_legacy_tenant_organization_scope"
down_revision = "0002_four_level_organizations"
branch_labels = None
depends_on = None

BUSINESS_TABLES = [
    "documents", "conversations", "messages", "user_memories", "chunk_vectors",
    "user_profiles", "llm_usage_logs", "llm_degradation_events", "usage_quotas",
    "usage_quota_reservations", "frontend_telemetry", "approval_policies",
    "domain_events", "notifications", "notification_endpoints", "notification_deliveries",
    "notification_preferences", "billing_invoices", "billing_invoice_lines",
    "billing_payments", "db_operation_log", "db_review_tasks", "db_backups",
    "db_backup_chunks", "db_execution_sagas", "db_rollback_records",
]


def _columns(bind, table):
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    restore_sqlite_audit_guard = False
    if bind.dialect.name == "sqlite" and "db_operation_log" in tables:
        trigger_names = {row[0] for row in bind.execute(sa.text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='db_operation_log'"
        ))}
        if "db_operation_log_no_update" in trigger_names:
            op.execute(sa.text("DROP TRIGGER db_operation_log_no_update"))
            restore_sqlite_audit_guard = True
    if "tenant_database_configs" in tables and "config_version" not in _columns(bind, "tenant_database_configs"):
        op.add_column("tenant_database_configs", sa.Column(
            "config_version", sa.Integer(), nullable=False, server_default="1"))
    if "tenant_organization_mappings" not in tables:
        op.create_table(
            "tenant_organization_mappings",
            sa.Column("tenant_id", sa.String(36), primary_key=True),
            sa.Column("organization_id", sa.String(36), nullable=False, unique=True),
            sa.Column("mapping_level", sa.String(20), nullable=False, server_default="company"),
            sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    for table in BUSINESS_TABLES:
        if table not in tables:
            continue
        columns = _columns(bind, table)
        for name in ("company_id", "organization_id", "membership_id"):
            if name not in columns:
                op.add_column(table, sa.Column(name, sa.String(36), nullable=True))
        op.execute(sa.text(
            f"UPDATE {table} SET company_id=tenant_id, organization_id=tenant_id "
            "WHERE tenant_id IS NOT NULL AND (company_id IS NULL OR organization_id IS NULL)"
        ))
        try:
            op.create_index(f"ix_{table}_organization_scope", table,
                            ["company_id", "organization_id", "membership_id"])
        except Exception:
            pass

    if "tenants" in tables:
        op.execute(sa.text("""
            INSERT INTO organization_units
                (id,name,level,parent_id,company_id,path,depth,active,pending_confirmation,
                 sort_order,context_version,created_at,updated_at)
            SELECT t.id,t.name,'company',NULL,t.id,t.id,1,t.active,1,0,1,t.created_at,t.created_at
              FROM tenants t
             WHERE NOT EXISTS (SELECT 1 FROM organization_units o WHERE o.id=t.id)
        """))
        op.execute(sa.text("""
            INSERT INTO tenant_organization_mappings
                (tenant_id,organization_id,mapping_level,confirmed,created_at)
            SELECT t.id,t.id,'company',0,t.created_at FROM tenants t
             WHERE NOT EXISTS (SELECT 1 FROM tenant_organization_mappings m WHERE m.tenant_id=t.id)
        """))
    if "memberships" in tables:
        op.execute(sa.text("""
            INSERT INTO organization_memberships
                (id,user_id,organization_id,organization_level,role,job_title,is_primary,
                 active,valid_from,valid_to,created_by,version,created_at,updated_at)
            SELECT id,user_id,tenant_id,'company',role,NULL,
                   CASE WHEN active=1 AND ROW_NUMBER() OVER (
                       PARTITION BY user_id ORDER BY active DESC,created_at,id)=1
                        THEN 1 ELSE 0 END,
                   active,NULL,NULL,NULL,1,created_at,created_at
              FROM memberships m
             WHERE NOT EXISTS (SELECT 1 FROM organization_memberships om WHERE om.id=m.id)
        """))
    # New writes synchronize on organization-scoped keys. Values are also
    # prefixed by organization for compatibility with legacy global indexes.
    for name, table, columns in [
        ("uq_org_review_idempotency", "db_review_tasks", ["organization_id", "idempotency_key"]),
        ("uq_org_quota_request", "usage_quota_reservations", ["organization_id", "request_id"]),
    ]:
        if table in tables:
            existing_names = {
                item["name"] for item in sa.inspect(bind).get_indexes(table)
            } | {
                item["name"] for item in sa.inspect(bind).get_unique_constraints(table)
            }
            if name in existing_names:
                continue
            try:
                op.create_unique_constraint(name, table, columns)
            except NotImplementedError:
                op.create_index(name, table, columns, unique=True)
            except Exception:
                pass
    if restore_sqlite_audit_guard:
        op.execute(sa.text(
            "CREATE TRIGGER db_operation_log_no_update BEFORE UPDATE ON db_operation_log "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
        ))


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for name, table in [
        ("uq_org_review_idempotency", "db_review_tasks"),
        ("uq_org_quota_request", "usage_quota_reservations"),
    ]:
        if table in tables:
            try:
                op.drop_index(name, table_name=table)
            except Exception:
                pass
    # Keep additive nullable scope/config columns during rollback. Legacy code
    # ignores them, while retaining them avoids destructive table rewrites and
    # preserves a lossless path for re-upgrade.
    if "tenant_organization_mappings" in tables:
        op.drop_table("tenant_organization_mappings")
