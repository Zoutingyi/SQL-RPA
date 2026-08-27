"""Dry-run and conservation checks for the user-api-v1 migration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unicodedata

from sqlalchemy import create_engine, inspect, text

V1_COLUMNS = {
    "username_normalized", "display_name", "phone", "is_platform_admin",
    "must_change_password", "password_changed_at", "created_by", "token_version",
    "profile_incomplete", "version",
}


def _sync_url(url: str) -> str:
    return (url.replace("sqlite+aiosqlite", "sqlite")
            .replace("mysql+aiomysql", "mysql+pymysql")
            .replace("postgresql+asyncpg", "postgresql+psycopg"))


def _normal(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def audit(database_url: str, phase: str, baseline: dict | None = None) -> dict:
    engine = create_engine(_sync_url(database_url))
    issues: list[dict] = []
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        if "users" not in tables:
            raise RuntimeError("users table is required")
        user_columns = {item["name"] for item in inspect(conn).get_columns("users")}
        rows = conn.execute(text(
            "SELECT id,username,password_hash FROM users ORDER BY id"
        )).mappings().all()
        normalized = [_normal(str(row["username"])) for row in rows]
        duplicate_names = sorted({name for name in normalized if normalized.count(name) > 1})
        if duplicate_names:
            issues.append({"type": "duplicate_normalized_username",
                           "count": len(duplicate_names)})
        password_checksum = hashlib.sha256("\n".join(
            f"{row['id']}:{row['password_hash']}" for row in rows).encode()).hexdigest()
        counts = {"users": len(rows)}
        if "organization_memberships" in tables:
            counts["organization_memberships"] = int(conn.execute(text(
                "SELECT COUNT(*) FROM organization_memberships")).scalar_one())
            orphan_count = int(conn.execute(text(
                "SELECT COUNT(*) FROM organization_memberships m "
                "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id=m.user_id)"
            )).scalar_one())
            if orphan_count:
                issues.append({"type": "orphan_organization_membership",
                               "count": orphan_count})
            duplicate_primary = conn.execute(text(
                "SELECT user_id,organization_level,COUNT(*) AS amount "
                "FROM organization_memberships WHERE active=1 AND is_primary=1 "
                "GROUP BY user_id,organization_level HAVING COUNT(*)>1"
            )).mappings().all()
            if duplicate_primary:
                issues.append({"type": "duplicate_active_primary",
                               "count": len(duplicate_primary)})
        if phase == "post":
            missing = sorted(V1_COLUMNS - user_columns)
            if missing:
                issues.append({"type": "missing_v1_columns", "columns": missing})
            else:
                null_normalized = int(conn.execute(text(
                    "SELECT COUNT(*) FROM users WHERE username_normalized IS NULL"
                )).scalar_one())
                if null_normalized:
                    issues.append({"type": "null_normalized_username",
                                   "count": null_normalized})
        conservation = {"users": True, "password_hashes": True,
                        "organization_memberships": True}
        if baseline:
            old_counts = baseline.get("counts", {})
            conservation["users"] = old_counts.get("users") == counts["users"]
            conservation["organization_memberships"] = (
                old_counts.get("organization_memberships", 0)
                == counts.get("organization_memberships", 0))
            conservation["password_hashes"] = (
                baseline.get("password_hash_checksum") == password_checksum)
            for name, conserved in conservation.items():
                if not conserved:
                    issues.append({"type": f"{name}_conservation_failed"})
        result = {"phase": phase, "counts": counts,
                  "password_hash_checksum": password_checksum,
                  "conservation": conservation, "issues": issues,
                  "status": "passed" if not issues else "blocked"}
        result["checksum"] = hashlib.sha256(json.dumps(
            result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    engine.dispose()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--phase", choices=("pre", "post"), required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    report = audit(args.database_url, args.phase, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": report["status"], "issues": len(report["issues"]),
                      "checksum": report["checksum"]}))
    return 2 if args.require_clean and report["status"] != "passed" else 0


if __name__ == "__main__":
    sys.exit(main())
