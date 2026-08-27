"""Create inactive placeholder tenants for traceable orphan-resource quarantine."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text

from organization_migration_check import BUSINESS_TABLES, _sync_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    engine = create_engine(_sync_url(args.database_url))
    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        existing = {row[0] for row in conn.execute(text("SELECT id FROM tenants"))}
        referenced: set[str] = set()
        for table in sorted(tables & set(BUSINESS_TABLES)):
            columns = {column["name"] for column in inspector.get_columns(table)}
            if "tenant_id" in columns:
                referenced.update(row[0] for row in conn.execute(text(
                    f"SELECT DISTINCT tenant_id FROM {table} WHERE tenant_id IS NOT NULL")))
        missing = sorted(referenced - existing)
        now = datetime.now(timezone.utc)
        for tenant_id in missing:
            conn.execute(text(
                "INSERT INTO tenants(id,name,active,created_at) "
                "VALUES (:id,:name,:active,:created_at)"), {
                    "id": tenant_id, "name": f"QUARANTINED-{tenant_id}",
                    "active": False, "created_at": now,
                })
    engine.dispose()
    print(f"quarantine_placeholder_tenants_created={len(missing)}")
    print("active=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
