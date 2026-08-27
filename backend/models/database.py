from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Initialize runtime helpers after Alembic has established the schema."""
    import models.schemas  # noqa: ensure all ORM models are loaded before create_all

    async with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            # Cross-row hierarchy invariants cannot be expressed as CHECK
            # constraints in SQLite, so enforce them with database triggers.
            for statement in [
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
            ]:
                await conn.exec_driver_sql(statement)

        # ── RAG Agent migrations ──
        for col, spec in [
            ("memory_type", "TEXT NOT NULL DEFAULT 'fact'"),
            ("deprecated", "INTEGER NOT NULL DEFAULT 0"),
            ("updated_at", "TIMESTAMP"),
        ]:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE user_memories ADD COLUMN {col} {spec}"
                )
            except Exception:
                pass

        try:
            await conn.exec_driver_sql("ALTER TABLE db_review_tasks ADD COLUMN execution_result JSON")
        except Exception:
            pass

        for statement in [
            "ALTER TABLE notification_deliveries ADD COLUMN claimed_at TIMESTAMP",
            "ALTER TABLE billing_invoices ADD COLUMN invoice_key TEXT",
            "ALTER TABLE users ADD COLUMN can_view_full_phone INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                await conn.exec_driver_sql(statement)
            except Exception:
                pass
        tenant_tables = [
            "documents", "conversations", "messages", "user_memories", "llm_usage_logs",
            "usage_quotas", "usage_quota_reservations", "domain_events", "notifications",
            "notification_endpoints", "notification_deliveries", "notification_preferences",
            "billing_invoices", "billing_invoice_lines", "billing_payments",
            "db_operation_log", "db_review_tasks", "db_backups",
            "chunk_vectors", "user_profiles", "llm_degradation_events",
            "frontend_telemetry", "approval_policies", "db_backup_chunks",
            "db_execution_sagas", "db_rollback_records",
        ]
        for table_name in tenant_tables:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
                )
            except Exception:
                pass
            for scope_column in ("company_id", "organization_id", "membership_id"):
                try:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {scope_column} TEXT"
                    )
                except Exception:
                    pass
            try:
                await conn.exec_driver_sql(
                    f"UPDATE {table_name} SET company_id=tenant_id, organization_id=tenant_id "
                    "WHERE company_id IS NULL OR organization_id IS NULL"
                )
            except Exception:
                pass
        try:
            await conn.exec_driver_sql(
                "ALTER TABLE tenant_database_configs ADD COLUMN config_version INTEGER NOT NULL DEFAULT 1"
            )
        except Exception:
            pass
        for table_name in ["documents", "user_memories"]:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table_name} ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'dev-user'"
                )
            except Exception:
                pass
        try:
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_billing_invoice_key "
                "ON billing_invoices(tenant_id, invoice_key) WHERE invoice_key IS NOT NULL"
            )
        except Exception:
            pass
        for statement in [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_org_review_idempotency "
            "ON db_review_tasks(organization_id,idempotency_key) WHERE idempotency_key IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_org_quota_request "
            "ON usage_quota_reservations(organization_id,request_id)",
        ]:
            try:
                await conn.exec_driver_sql(statement)
            except Exception:
                pass

        try:
            await conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN last_extracted_at TIMESTAMP"
            )
        except Exception:
            pass
        try:
            await conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'dev-user'"
            )
        except Exception:
            pass

        # ── RPA database migrations ──
        for col, spec in [
            ("affected_rows", "INTEGER NOT NULL DEFAULT 0"),
            ("table_name", "TEXT"),
            ("executed_by", "TEXT DEFAULT 'agent'"),
            ("submitted_by", "TEXT"),
            ("approved_by", "TEXT"),
            ("sequence", "INTEGER DEFAULT 0"),
            ("prev_hash", "TEXT"),
            ("entry_hash", "TEXT"),
            ("error_message", "TEXT"),
            ("updated_at", "TIMESTAMP"),
            ("reviewer_note", "TEXT"),
            ("review_id", "TEXT"),
        ]:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE db_operation_log ADD COLUMN {col} {spec}"
                )
            except Exception:
                pass

        for col, spec in [
            ("user_id", "TEXT"), ("request_id", "TEXT"),
            ("provider", "TEXT"), ("cost_usd", "REAL NOT NULL DEFAULT 0"),
        ]:
            try:
                await conn.exec_driver_sql(f"ALTER TABLE llm_usage_logs ADD COLUMN {col} {spec}")
            except Exception:
                pass

        for col, spec in [
            ("operation_type", "TEXT NOT NULL DEFAULT 'DELETE'"),
            ("affected_rows", "INTEGER NOT NULL DEFAULT 0"),
            ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ("expired_at", "TIMESTAMP"),
        ]:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE db_backups ADD COLUMN {col} {spec}"
                )
            except Exception:
                pass

        for col, spec in [
            ("idempotency_key", "TEXT"),
            ("submitted_by", "TEXT"),
            ("approved_by", "TEXT"),
            ("reviewed_at", "TIMESTAMP"),
            ("first_approver_id", "TEXT"),
            ("first_approver_note", "TEXT"),
            ("first_approved_at", "TIMESTAMP"),
            ("second_approver_id", "TEXT"),
            ("second_approver_note", "TEXT"),
            ("second_approved_at", "TIMESTAMP"),
            ("assigned_to", "TEXT"), ("expires_at", "TIMESTAMP"),
            ("policy_id", "TEXT"), ("policy_version", "INTEGER"),
            ("required_approvals", "INTEGER NOT NULL DEFAULT 1"),
            ("risk_score", "INTEGER NOT NULL DEFAULT 0"),
            ("risk_factors", "JSON NOT NULL DEFAULT '[]'"),
        ]:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE db_review_tasks ADD COLUMN {col} {spec}"
                )
            except Exception:
                pass

        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_db_review_tasks_idempotency_key "
            "ON db_review_tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        # One-time normalization for legacy rows that used sequence=0. Triggers
        # are removed only inside this schema migration transaction, then the
        # complete chain is rebuilt before uniqueness/immutability are enabled.
        rows = (await conn.exec_driver_sql(
            "SELECT * FROM db_operation_log ORDER BY created_at, id"
        )).mappings().all()
        sequences = [row["sequence"] for row in rows]
        needs_sequence_migration = bool(rows) and (
            any(value is None for value in sequences)
            or len(set(sequences)) != len(sequences)
            or sorted(sequences) != list(range(1, len(sequences) + 1))
        )
        if needs_sequence_migration:
            await conn.exec_driver_sql("DROP TRIGGER IF EXISTS db_operation_log_no_update")
            await conn.exec_driver_sql("DROP TRIGGER IF EXISTS db_operation_log_no_delete")
            await conn.exec_driver_sql("DROP INDEX IF EXISTS uq_db_operation_log_sequence")
            import hashlib
            import json
            from datetime import datetime

            previous_hash = None
            for sequence, row in enumerate(rows, 1):
                created = row["created_at"]
                if isinstance(created, str):
                    created = datetime.fromisoformat(created).isoformat()
                else:
                    created = created.isoformat()
                canonical = json.dumps({
                    "sequence": sequence, "operation_type": row["operation_type"],
                    "sql_text": row["sql_text"], "affected_rows": row["affected_rows"],
                    "table_name": row["table_name"] or "", "backup_id": row["backup_id"],
                    "status": row["status"], "executed_by": row["executed_by"],
                    "submitted_by": row["submitted_by"], "approved_by": row["approved_by"],
                    "reviewer_note": row["reviewer_note"], "created_at": created,
                    "prev_hash": previous_hash,
                }, ensure_ascii=False, sort_keys=True, default=str)
                entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                await conn.exec_driver_sql(
                    "UPDATE db_operation_log SET sequence=?, prev_hash=?, entry_hash=? WHERE id=?",
                    (sequence, previous_hash, entry_hash, row["id"]),
                )
                previous_hash = entry_hash
        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_db_operation_log_sequence "
            "ON db_operation_log(sequence)"
        )

        # ── Optimizations ──
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=30000")

        # Audit records are append-only. Only INSERT is permitted at DB level.
        await conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS db_operation_log_no_update "
            "BEFORE UPDATE ON db_operation_log BEGIN "
            "SELECT RAISE(ABORT, 'audit log is append-only'); END"
        )
        await conn.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS db_operation_log_no_delete "
            "BEFORE DELETE ON db_operation_log BEGIN "
            "SELECT RAISE(ABORT, 'audit log is append-only'); END"
        )

        # ── FTS5 virtual table ──
        await conn.execute(sa_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "USING fts5(chunk_id, document_id, content, tokenize='unicode61')"
        ))
        await conn.execute(sa_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_tenant "
            "USING fts5(chunk_id UNINDEXED, tenant_id UNINDEXED, document_id UNINDEXED, "
            "content, tokenize='unicode61')"
        ))

        # ── User memories FTS5 ──
        await conn.execute(sa_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS user_memories_fts "
            "USING fts5(memory_id, content, tokenize='unicode61')"
        ))


async def get_db():
    """FastAPI dependency: yields an async database session."""
    async with async_session() as session:
        yield session
