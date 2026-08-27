"""Add independent permission for full user phone access.

Revision ID: 0008_user_phone_permission
Revises: 0007_user_delivery_hardening
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_user_phone_permission"
down_revision = "0007_user_delivery_hardening"
branch_labels = None
depends_on = None


def upgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "can_view_full_phone" not in columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("can_view_full_phone", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("can_view_full_phone")
