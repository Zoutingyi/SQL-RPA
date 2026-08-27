"""Encrypt user phone storage and enforce user delivery hardening.

Revision ID: 0007_user_delivery_hardening
Revises: 0006_user_identity_v1
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_user_delivery_hardening"
down_revision = "0006_user_identity_v1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("users")}
    indexes = {item.get("name") for item in inspector.get_indexes("users")}
    with op.batch_alter_table("users") as batch:
        if "phone_hash" not in columns:
            batch.add_column(sa.Column("phone_hash", sa.String(64), nullable=True))
        if "ix_users_phone_hash" not in indexes:
            batch.create_index("ix_users_phone_hash", ["phone_hash"])

    from user_pii import encrypt_phone
    rows = bind.execute(sa.text(
        "SELECT id,phone FROM users WHERE phone IS NOT NULL AND phone<>''"
    )).all()
    for user_id, phone in rows:
        encrypted, lookup_hash = encrypt_phone(phone)
        bind.execute(sa.text(
            "UPDATE users SET phone=:phone,phone_hash=:phone_hash WHERE id=:id"
        ), {"phone": encrypted, "phone_hash": lookup_hash, "id": user_id})


def downgrade():
    from user_pii import decrypt_phone
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id,phone FROM users WHERE phone IS NOT NULL AND phone<>''"
    )).all()
    for user_id, phone in rows:
        bind.execute(sa.text("UPDATE users SET phone=:phone WHERE id=:id"),
                     {"phone": decrypt_phone(phone), "id": user_id})
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_phone_hash")
        batch.drop_column("phone_hash")
