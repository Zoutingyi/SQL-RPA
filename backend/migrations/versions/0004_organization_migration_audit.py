"""Add migration preflight/quarantine/audit support and repair invalid primaries.

Revision ID: 0004_organization_migration_audit
Revises: 0003_legacy_tenant_organization_scope
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_organization_migration_audit"
down_revision = "0003_legacy_tenant_organization_scope"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "organization_migration_runs" not in tables:
        op.create_table(
            "organization_migration_runs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("phase", sa.String(20), nullable=False, index=True),
            sa.Column("source_label", sa.String(200), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, index=True),
            sa.Column("counts", sa.JSON(), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if "organization_migration_issues" not in tables:
        op.create_table(
            "organization_migration_issues",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), nullable=False, index=True),
            sa.Column("issue_type", sa.String(80), nullable=False, index=True),
            sa.Column("source_table", sa.String(100), nullable=False),
            sa.Column("source_id", sa.String(100), nullable=True),
            sa.Column("tenant_id", sa.String(36), nullable=True, index=True),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if "organization_memberships" in tables:
        # A disabled assignment can never remain primary.
        op.execute(sa.text(
            "UPDATE organization_memberships SET is_primary=0, version=version+1 "
            "WHERE active=0 AND is_primary=1"
        ))
        # Deterministically repair users/levels that have active assignments but no primary.
        op.execute(sa.text("""
            UPDATE organization_memberships AS candidate
               SET is_primary=1, version=version+1
             WHERE candidate.active=1
               AND candidate.id=(
                   SELECT choice.id FROM organization_memberships AS choice
                    WHERE choice.user_id=candidate.user_id
                      AND choice.organization_level=candidate.organization_level
                      AND choice.active=1
                    ORDER BY choice.created_at,choice.id LIMIT 1)
               AND NOT EXISTS (
                   SELECT 1 FROM organization_memberships AS current
                    WHERE current.user_id=candidate.user_id
                      AND current.organization_level=candidate.organization_level
                      AND current.active=1 AND current.is_primary=1)
        """))


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "organization_migration_issues" in tables:
        op.drop_table("organization_migration_issues")
    if "organization_migration_runs" in tables:
        op.drop_table("organization_migration_runs")
