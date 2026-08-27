"""Add V1 global user identity and creation idempotency fields.

Revision ID: 0006_user_identity_v1
Revises: 0005_legacy_mapping_reconciliation
"""
import unicodedata

from alembic import op
import sqlalchemy as sa

revision = "0006_user_identity_v1"
down_revision = "0005_legacy_mapping_reconciliation"
branch_labels = None
depends_on = None


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {item["name"] for item in inspector.get_columns("users")}
    users = sa.table("users", sa.column("id"), sa.column("username"),
                     sa.column("password_hash"))
    rows = list(bind.execute(sa.select(users.c.id, users.c.username, users.c.password_hash)))
    normalized = [_normalize(row.username) for row in rows]
    if len(normalized) != len(set(normalized)):
        raise RuntimeError("User migration blocked: duplicate normalized usernames exist")

    additions = [
        sa.Column("username_normalized", sa.String(64), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("profile_incomplete", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    ]
    missing = [column for column in additions if column.name not in existing_columns]
    if missing:
        with op.batch_alter_table("users") as batch:
            for column in missing:
                batch.add_column(column)

    for row, value in zip(rows, normalized):
        bind.execute(sa.text(
            "UPDATE users SET username_normalized=:normalized, "
            "display_name=:display_name WHERE id=:id"
        ), {"normalized": value, "display_name": row.username, "id": row.id})

    inspector = sa.inspect(bind)
    unique_names = {item.get("name") for item in inspector.get_unique_constraints("users")}
    index_names = {item.get("name") for item in inspector.get_indexes("users")}
    with op.batch_alter_table("users") as batch:
        if "username_normalized" in {column.name for column in missing}:
            batch.alter_column("username_normalized", nullable=False)
        if "uq_users_username_normalized" not in unique_names:
            batch.create_unique_constraint("uq_users_username_normalized", ["username_normalized"])
        if "ix_users_is_platform_admin" not in index_names:
            batch.create_index("ix_users_is_platform_admin", ["is_platform_admin"])

    # init_db/create_all from a mixed-version deployment may have created this
    # new table before Alembic advanced. Accept only the exact expected schema;
    # never fail halfway through SQLite's non-transactional DDL for that case.
    inspector = sa.inspect(bind)
    if "user_create_idempotency" not in set(inspector.get_table_names()):
        op.create_table(
            "user_create_idempotency",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("actor_id", sa.String(36), nullable=False),
            sa.Column("idempotency_key", sa.String(200), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_user_create_actor_key"),
        )
        op.create_index("ix_user_create_idempotency_actor_id", "user_create_idempotency", ["actor_id"])
        op.create_index("ix_user_create_idempotency_user_id", "user_create_idempotency", ["user_id"])
    else:
        expected = {"id", "actor_id", "idempotency_key", "request_hash", "user_id",
                    "response_json", "created_at"}
        actual = {column["name"] for column in inspector.get_columns(
            "user_create_idempotency")}
        unique_columns = {tuple(item.get("column_names") or []) for item in
                          inspector.get_unique_constraints("user_create_idempotency")}
        if actual != expected or ("actor_id", "idempotency_key") not in unique_columns:
            raise RuntimeError(
                "Existing user_create_idempotency table is incompatible with migration 0006"
            )


def downgrade():
    op.drop_table("user_create_idempotency")
    inspector = sa.inspect(op.get_bind())
    indexes = {item.get("name") for item in inspector.get_indexes("users")}
    constraints = {item.get("name") for item in inspector.get_unique_constraints("users")}
    with op.batch_alter_table("users") as batch:
        for index in ("ix_users_is_platform_admin", "ix_users_username_normalized"):
            if index in indexes:
                batch.drop_index(index)
        if "uq_users_username_normalized" in constraints:
            batch.drop_constraint("uq_users_username_normalized", type_="unique")
        for column in ("version", "profile_incomplete", "token_version", "created_by",
                       "password_changed_at", "must_change_password", "is_platform_admin",
                       "phone", "display_name", "username_normalized"):
            batch.drop_column(column)
