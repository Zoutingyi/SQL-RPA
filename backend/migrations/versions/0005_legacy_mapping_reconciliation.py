"""Reconcile incremental tenant and legacy membership mappings.

Revision ID: 0005_legacy_mapping_reconciliation
Revises: 0004_organization_migration_audit
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_legacy_mapping_reconciliation"
down_revision = "0004_organization_migration_audit"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "legacy_membership_mappings" not in tables:
        op.create_table(
            "legacy_membership_mappings",
            sa.Column("legacy_membership_id", sa.String(36), primary_key=True),
            sa.Column("organization_membership_id", sa.String(36), nullable=False, index=True),
            sa.Column("mapping_reason", sa.String(40), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if {"tenants", "organization_units"} <= tables:
        op.execute(sa.text("""
            INSERT INTO organization_units
                (id,name,level,parent_id,company_id,path,depth,active,pending_confirmation,
                 sort_order,context_version,created_at,updated_at)
            SELECT t.id,t.name,'company',NULL,t.id,t.id,1,t.active,1,0,1,t.created_at,t.created_at
              FROM tenants t
             WHERE NOT EXISTS (SELECT 1 FROM organization_units o WHERE o.id=t.id)
        """))
    if {"tenants", "tenant_organization_mappings"} <= tables:
        op.execute(sa.text("""
            INSERT INTO tenant_organization_mappings
                (tenant_id,organization_id,mapping_level,confirmed,created_at)
            SELECT t.id,t.id,'company',0,t.created_at FROM tenants t
             WHERE NOT EXISTS (SELECT 1 FROM tenant_organization_mappings m WHERE m.tenant_id=t.id)
        """))
    if {"memberships", "organization_memberships"} <= tables:
        # Exact-ID rows retain their identity; user/org conflicts map to the
        # already-established organization membership without duplicating it.
        op.execute(sa.text("""
            INSERT INTO legacy_membership_mappings
                (legacy_membership_id,organization_membership_id,mapping_reason,created_at)
            SELECT m.id,om.id,
                   CASE WHEN m.id=om.id THEN 'same_id' ELSE 'existing_user_org' END,
                   m.created_at
              FROM memberships m
              JOIN organization_memberships om
                ON om.user_id=m.user_id AND om.organization_id=m.tenant_id
             WHERE NOT EXISTS (SELECT 1 FROM legacy_membership_mappings lm
                                WHERE lm.legacy_membership_id=m.id)
        """))


def downgrade():
    if "legacy_membership_mappings" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("legacy_membership_mappings")
