"""Add four-level organization units and memberships.

Revision ID: 0002_four_level_organizations
Revises: 0001_baseline
"""
from alembic import op
from sqlalchemy import text

from models.schemas import OrganizationMembership, OrganizationUnit

revision = "0002_four_level_organizations"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    OrganizationUnit.__table__.create(bind, checkfirst=True)
    OrganizationMembership.__table__.create(bind, checkfirst=True)
    if bind.dialect.name == "sqlite":
        statements = [
            "CREATE TRIGGER IF NOT EXISTS organization_parent_level_insert "
            "BEFORE INSERT ON organization_units WHEN NEW.parent_id IS NOT NULL BEGIN "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM organization_units p WHERE p.id=NEW.parent_id "
            "AND ((p.level='company' AND NEW.level='department') OR "
            "(p.level='department' AND NEW.level='group') OR "
            "(p.level='group' AND NEW.level='individual')) AND p.company_id=NEW.company_id) "
            "THEN RAISE(ABORT, 'invalid organization hierarchy') END; END",
            "CREATE TRIGGER IF NOT EXISTS organization_parent_level_update "
            "BEFORE UPDATE OF parent_id, level, company_id ON organization_units "
            "WHEN NEW.parent_id IS NOT NULL BEGIN "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM organization_units p WHERE p.id=NEW.parent_id "
            "AND ((p.level='company' AND NEW.level='department') OR "
            "(p.level='department' AND NEW.level='group') OR "
            "(p.level='group' AND NEW.level='individual')) AND p.company_id=NEW.company_id) "
            "THEN RAISE(ABORT, 'invalid organization hierarchy') END; END",
            "CREATE TRIGGER IF NOT EXISTS organization_no_cycle_update "
            "BEFORE UPDATE OF parent_id ON organization_units WHEN NEW.parent_id IS NOT NULL BEGIN "
            "WITH RECURSIVE descendants(id) AS (SELECT id FROM organization_units WHERE parent_id=NEW.id "
            "UNION ALL SELECT u.id FROM organization_units u JOIN descendants d ON u.parent_id=d.id) "
            "SELECT CASE WHEN NEW.parent_id IN (SELECT id FROM descendants) "
            "THEN RAISE(ABORT, 'organization cycle') END; END",
        ]
        for statement in statements:
            bind.execute(text(statement))
    elif bind.dialect.name == "postgresql":
        bind.execute(text("""
            CREATE OR REPLACE FUNCTION validate_organization_hierarchy() RETURNS trigger AS $$
            DECLARE parent_level text; parent_company text;
            BEGIN
              IF NEW.parent_id IS NULL THEN RETURN NEW; END IF;
              SELECT level, company_id INTO parent_level, parent_company
                FROM organization_units WHERE id = NEW.parent_id FOR SHARE;
              IF parent_company IS NULL OR parent_company <> NEW.company_id OR NOT (
                 (parent_level='company' AND NEW.level='department') OR
                 (parent_level='department' AND NEW.level='group') OR
                 (parent_level='group' AND NEW.level='individual')) THEN
                RAISE EXCEPTION 'invalid organization hierarchy';
              END IF;
              IF EXISTS (WITH RECURSIVE descendants(id) AS (
                  SELECT id FROM organization_units WHERE parent_id=NEW.id
                  UNION ALL SELECT u.id FROM organization_units u JOIN descendants d ON u.parent_id=d.id)
                  SELECT 1 FROM descendants WHERE id=NEW.parent_id) THEN
                RAISE EXCEPTION 'organization cycle';
              END IF;
              RETURN NEW;
            END; $$ LANGUAGE plpgsql
        """))
        bind.execute(text("DROP TRIGGER IF EXISTS organization_hierarchy_guard ON organization_units"))
        bind.execute(text("CREATE TRIGGER organization_hierarchy_guard BEFORE INSERT OR UPDATE OF parent_id, level, company_id ON organization_units FOR EACH ROW EXECUTE FUNCTION validate_organization_hierarchy()"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(text("DROP TRIGGER IF EXISTS organization_hierarchy_guard ON organization_units"))
        bind.execute(text("DROP FUNCTION IF EXISTS validate_organization_hierarchy()"))
    OrganizationMembership.__table__.drop(bind, checkfirst=True)
    OrganizationUnit.__table__.drop(bind, checkfirst=True)
