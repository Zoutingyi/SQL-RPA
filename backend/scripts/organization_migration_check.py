"""Preflight and conservation checker for four-level organization migration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

from sqlalchemy import create_engine, inspect, text

BUSINESS_TABLES = [
    "documents", "conversations", "messages", "user_memories", "chunk_vectors",
    "user_profiles", "llm_usage_logs", "llm_degradation_events", "usage_quotas",
    "usage_quota_reservations", "frontend_telemetry", "approval_policies",
    "domain_events", "notifications", "notification_endpoints", "notification_deliveries",
    "notification_preferences", "billing_invoices", "billing_invoice_lines",
    "billing_payments", "db_operation_log", "db_review_tasks", "db_backups",
    "db_backup_chunks", "db_execution_sagas", "db_rollback_records",
]


def _sync_url(url: str) -> str:
    return (url.replace("sqlite+aiosqlite", "sqlite")
            .replace("mysql+aiomysql", "mysql+pymysql")
            .replace("postgresql+asyncpg", "postgresql+psycopg"))


def _count(conn, table: str, where: str = "1=1") -> int:
    return int(conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}")).scalar_one())


def audit(database_url: str, phase: str, source_label: str, *, record: bool = False) -> dict:
    engine = create_engine(_sync_url(database_url))
    run_id = str(uuid.uuid4())
    issues: list[dict] = []
    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        counts = {table: _count(conn, table) for table in sorted(tables & set(BUSINESS_TABLES))}
        for table in sorted(tables & set(BUSINESS_TABLES)):
            table_inspector = inspect(conn)
            column_rows = table_inspector.get_columns(table)
            columns = {column["name"] for column in column_rows}
            primary_columns = table_inspector.get_pk_constraint(table).get("constrained_columns") or []
            resource_key = primary_columns[0] if primary_columns else next(iter(columns))
            if "tenant_id" in columns and "tenants" in tables:
                rows = conn.execute(text(
                    f"SELECT {resource_key},tenant_id FROM {table} r WHERE tenant_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id=r.tenant_id) LIMIT 1000"
                )).all()
                issues.extend({"issue_type": "orphan_resource_tenant", "source_table": table,
                    "source_id": str(row[0]), "tenant_id": row[1]} for row in rows)
            if phase == "post" and {"tenant_id", "organization_id", "company_id"} <= columns:
                missing = conn.execute(text(
                    f"SELECT {resource_key},tenant_id FROM {table} WHERE tenant_id IS NOT NULL AND "
                    "(organization_id IS NULL OR company_id IS NULL) LIMIT 1000")).all()
                issues.extend({"issue_type": "missing_organization_scope", "source_table": table,
                    "source_id": str(row[0]), "tenant_id": row[1]} for row in missing)
        if "memberships" in tables:
            orphan_members = conn.execute(text(
                "SELECT m.id,m.tenant_id FROM memberships m "
                "WHERE NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id=m.tenant_id) "
                "OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id=m.user_id) LIMIT 1000"
            )).all()
            issues.extend({"issue_type": "orphan_membership", "source_table": "memberships",
                "source_id": str(row[0]), "tenant_id": row[1]} for row in orphan_members)
        if phase == "post" and "organization_memberships" in tables:
            invalid_primary = conn.execute(text(
                "SELECT id,organization_id FROM organization_memberships "
                "WHERE active=0 AND is_primary=1 LIMIT 1000")).all()
            issues.extend({"issue_type": "inactive_primary", "source_table": "organization_memberships",
                "source_id": str(row[0]), "tenant_id": row[1]} for row in invalid_primary)
            bad_primary_count = conn.execute(text(
                "SELECT user_id,organization_level FROM organization_memberships WHERE active=1 "
                "GROUP BY user_id,organization_level HAVING SUM(CASE WHEN is_primary=1 THEN 1 ELSE 0 END)<>1"
            )).all()
            issues.extend({"issue_type": "primary_count_invalid", "source_table": "organization_memberships",
                "source_id": str(row[0]), "tenant_id": None, "organization_level": row[1]}
                for row in bad_primary_count)
        conservation = {}
        if phase == "post" and {"tenants", "tenant_organization_mappings"} <= tables:
            conservation["tenant_mapping"] = {
                "source": _count(conn, "tenants"),
                "target": int(conn.execute(text(
                    "SELECT COUNT(*) FROM tenants t JOIN tenant_organization_mappings m "
                    "ON m.tenant_id=t.id")).scalar_one()),
            }
        if phase == "post" and {"memberships", "organization_memberships"} <= tables:
            if "legacy_membership_mappings" in tables:
                mapped_memberships = int(conn.execute(text(
                    "SELECT COUNT(*) FROM memberships m JOIN legacy_membership_mappings lm "
                    "ON lm.legacy_membership_id=m.id")).scalar_one())
            else:
                mapped_memberships = int(conn.execute(text(
                    "SELECT COUNT(*) FROM memberships m JOIN organization_memberships om "
                    "ON om.id=m.id")).scalar_one())
            conservation["membership"] = {
                "source": _count(conn, "memberships"),
                "target": mapped_memberships,
            }
        conserved = all(item["source"] == item["target"] for item in conservation.values())
        payload = {"run_id": run_id, "phase": phase, "source_label": source_label,
                   "counts": counts, "conservation": conservation,
                   "conserved": conserved, "issues": issues,
                   "status": "passed" if not issues and conserved else "quarantined"}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        payload["checksum"] = hashlib.sha256(canonical.encode()).hexdigest()
        if record and {"organization_migration_runs", "organization_migration_issues"} <= tables:
            conn.execute(text("INSERT INTO organization_migration_runs "
                "(id,phase,source_label,status,counts,checksum) "
                "VALUES (:id,:phase,:label,:status,:counts,:checksum)"), {
                "id": run_id, "phase": phase, "label": source_label,
                "status": payload["status"], "counts": json.dumps(counts),
                "checksum": payload["checksum"]})
            for issue in issues:
                conn.execute(text("INSERT INTO organization_migration_issues "
                    "(id,run_id,issue_type,source_table,source_id,tenant_id,details,resolved) "
                    "VALUES (:id,:run,:kind,:table,:source,:tenant,:details,0)"), {
                    "id": str(uuid.uuid4()), "run": run_id, "kind": issue["issue_type"],
                    "table": issue["source_table"], "source": issue.get("source_id"),
                    "tenant": issue.get("tenant_id"), "details": json.dumps(issue)})
    engine.dispose()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--phase", choices=("pre", "post"), required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    report = audit(args.database_url, args.phase, args.source_label, record=args.record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({"status": report["status"], "issues": len(report["issues"]),
                      "checksum": report["checksum"]}))
    return 2 if args.require_clean and report["status"] != "passed" else 0


if __name__ == "__main__":
    sys.exit(main())
