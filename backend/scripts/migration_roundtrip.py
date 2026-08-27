"""Exercise legacy-user upgrade, downgrade and re-upgrade on a disposable database."""

from __future__ import annotations

import argparse
import hashlib
import os
import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def scalar(engine, statement: str, **params):
    with engine.begin() as connection:
        return connection.execute(sa.text(statement), params).scalar_one()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("SQL_RPA_ALEMBIC_DATABASE_URL"))
    args = parser.parse_args()
    if not args.url:
        raise SystemExit("--url or SQL_RPA_ALEMBIC_DATABASE_URL is required")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", args.url)
    engine = sa.create_engine(args.url)
    user_id = str(uuid.uuid4())
    username = "Legacy.Acceptance"
    password_hash = "legacy$" + hashlib.sha256(user_id.encode()).hexdigest()

    command.downgrade(config, "0005")
    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO users
                (id,username,password_hash,role,is_active,created_at,updated_at)
            VALUES
                (:id,:username,:password_hash,'viewer',true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """), {"id": user_id, "username": username, "password_hash": password_hash})

    command.upgrade(config, "0006")
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE users SET phone=:phone WHERE id=:id"),
                           {"phone": "13800138000", "id": user_id})
    command.upgrade(config, "head")
    with engine.begin() as connection:
        row = connection.execute(sa.text("""
            SELECT username_normalized,password_hash,phone,phone_hash
              FROM users WHERE id=:id
        """), {"id": user_id}).mappings().one()
    assert row["username_normalized"] == username.casefold()
    assert row["password_hash"] == password_hash
    assert str(row["phone"]).startswith("ENC:v")
    assert len(str(row["phone_hash"])) == 64

    command.downgrade(config, "0005")
    assert scalar(engine, "SELECT COUNT(*) FROM users WHERE id=:id", id=user_id) == 1
    assert scalar(engine, "SELECT password_hash FROM users WHERE id=:id", id=user_id) == password_hash
    command.upgrade(config, "head")
    assert scalar(engine, "SELECT COUNT(*) FROM users WHERE id=:id", id=user_id) == 1
    engine.dispose()
    print("migration-roundtrip: passed; user count and password hash preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
