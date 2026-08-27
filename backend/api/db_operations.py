"""Database operation review API — SQL preview, review queue, execution, rollback, audit logs."""

import json
import logging
import re
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError

from db_connector.safety import SafetyChecker
from db_connector.backup import BackupManager
from db_connector.factory import get_connector, create_connector
from auth import AuthUser, auth_enabled, get_current_user, get_tenant_id, require_roles
from models.database import async_session
from models.schemas import DbBackup, DbExecutionSaga, DbOperationLog, DbReviewTask, DbRollbackRecord, DomainEvent, Membership
from utils.masking import mask_rows
from audit import verify_audit_chain
from config import settings

logger = logging.getLogger("sql_rpa")

router = APIRouter(prefix="/api/db_operations", tags=["db_operations"])

_safety = SafetyChecker()


# ── Review task CRUD (DB-backed, shared with agent/tools/database.py) ──

def _task_to_dict(task: DbReviewTask) -> dict:
    preview_columns = json.loads(task.preview_columns) if task.preview_columns else []
    preview_rows = json.loads(task.preview_rows) if task.preview_rows else []
    return {
        "id": task.id,
        "sql": task.sql,
        "reason": task.reason or "",
        "operation_type": task.operation_type,
        "affected_table": task.affected_table or "unknown",
        "affected_rows": task.affected_rows or 0,
        "risk_score": task.risk_score or 0,
        "risk_factors": task.risk_factors or [],
        "backup_id": task.backup_id,
        "status": task.status,
        "preview_columns": preview_columns,
        "preview_rows": preview_rows,
        "execution_result": task.execution_result or None,
        "columns": preview_columns,
        "has_backup": task.operation_type in ("INSERT", "UPDATE", "DELETE")
        and (task.affected_rows or 0) > 0,
        "safety_message": _safety.check(task.sql).message,
        "reviewer_note": task.reviewer_note or "",
        "submitted_by": task.submitted_by or "",
        "approved_by": task.approved_by or "",
        "reviewed_at": task.reviewed_at.isoformat() if task.reviewed_at else "",
        "first_approver_id": task.first_approver_id or "",
        "first_approver_note": task.first_approver_note or "",
        "first_approved_at": task.first_approved_at.isoformat() if task.first_approved_at else "",
        "second_approver_id": task.second_approver_id or "",
        "second_approver_note": task.second_approver_note or "",
        "second_approved_at": task.second_approved_at.isoformat() if task.second_approved_at else "",
        "assigned_to": task.assigned_to or "",
        "expires_at": task.expires_at.isoformat() if task.expires_at else "",
        "policy_id": task.policy_id,
        "policy_version": task.policy_version,
        "required_approvals": task.required_approvals or 1,
        "created_at": task.created_at.isoformat() if task.created_at else "",
    }


async def _create_review_task(
    sql: str, reason: str, operation_type: str,
    affected_table: str, affected_rows: int,
    preview_columns: list[str], preview_rows: list[list],
    submitted_by: str | None = None,
    idempotency_key: str | None = None,
    policy_id: str | None = None, policy_version: int | None = None,
    required_approvals: int = 1,
    risk_score: int = 0, risk_factors: list[str] | None = None,
    tenant_id: str = "default",
    organization_id: str | None = None,
    membership_id: str | None = None,
) -> dict:
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        task = DbReviewTask(
            id=task_id, tenant_id=tenant_id, organization_id=organization_id,
            membership_id=membership_id, sql=sql, reason=reason,
            operation_type=operation_type, affected_table=affected_table,
            affected_rows=affected_rows, status="awaiting_review",
            preview_columns=json.dumps(preview_columns) if preview_columns else None,
            preview_rows=json.dumps(preview_rows) if preview_rows else None,
            submitted_by=submitted_by, idempotency_key=idempotency_key,
            policy_id=policy_id, policy_version=policy_version,
            required_approvals=required_approvals,
            risk_score=risk_score, risk_factors=risk_factors or [],
            created_at=now, updated_at=now,
            expires_at=now + timedelta(hours=settings.review_expiry_hours),
        )
        session.add(task)
        await session.commit()

    return {
        "id": task_id, "sql": sql, "reason": reason,
        "operation_type": operation_type, "affected_table": affected_table,
        "affected_rows": affected_rows, "backup_id": None,
        "risk_score": risk_score, "risk_factors": risk_factors or [],
        "status": "awaiting_review",
        "preview_columns": preview_columns,
        "preview_rows": preview_rows,
        "submitted_by": submitted_by or "",
        "approved_by": "",
        "reviewed_at": "",
        "created_at": now.isoformat(),
    }


async def _get_review_task(task_id: str) -> dict | None:
    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(get_tenant_id())
    async with async_session() as session:
        result = await session.execute(
            select(DbReviewTask).where(DbReviewTask.id == task_id,
                                       DbReviewTask.tenant_id.in_(scope_ids))
        )
        task = result.scalar_one_or_none()
        if not task:
            return None
        return _task_to_dict(task)


async def _update_review_task_status(
    task_id: str, status: str, backup_id: str | None = None,
    reviewer_note: str = "", approved_by: str | None = None,
    reviewed_at: datetime | None = None,
) -> bool:
    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(get_tenant_id())
    async with async_session() as session:
        result = await session.execute(
            select(DbReviewTask).where(DbReviewTask.id == task_id,
                                       DbReviewTask.tenant_id.in_(scope_ids))
        )
        task = result.scalar_one_or_none()
        if not task:
            return False
        task.status = status
        task.updated_at = datetime.now(timezone.utc)
        if backup_id:
            task.backup_id = backup_id
        if reviewer_note:
            task.reviewer_note = reviewer_note
        if approved_by:
            task.approved_by = approved_by
        if reviewed_at:
            task.reviewed_at = reviewed_at
        await session.commit()
    return True


async def _transition_review_task(
    task_id: str,
    from_statuses: list[str],
    to_status: str,
    **fields,
) -> bool:
    """Atomically transition a review task only if it is still in an allowed state."""
    values = {"status": to_status, "updated_at": datetime.now(timezone.utc)}
    values.update(fields)
    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(get_tenant_id())
    event_tenant_id = None
    async with async_session() as session:
        event_tenant_id = await session.scalar(select(DbReviewTask.tenant_id).where(
            DbReviewTask.id == task_id, DbReviewTask.tenant_id.in_(scope_ids)))
        result = await session.execute(
            update(DbReviewTask)
            .where(
                DbReviewTask.id == task_id,
                DbReviewTask.tenant_id.in_(scope_ids),
                DbReviewTask.status.in_(from_statuses),
            )
            .values(**values)
        )
        await session.commit()
        changed = result.rowcount == 1
    if changed:
        await _emit_domain_event(task_id, f"review.{to_status}",
                                 {"from": from_statuses, "to": to_status},
                                 tenant_id=event_tenant_id)
    return changed


async def _emit_domain_event(review_id: str, event_type: str, payload: dict,
                             tenant_id: str | None = None) -> None:
    tenant_id = tenant_id or get_tenant_id()
    async with async_session() as session:
        session.add(DomainEvent(
            id=str(uuid.uuid4()), tenant_id=tenant_id, aggregate_type="db_review",
            aggregate_id=review_id, event_type=event_type, payload=payload,
        ))
        await session.commit()
    from notifications import publish_notification
    await publish_notification(event_type, {"review_id": review_id, **payload},
                               tenant_id=tenant_id)


async def _create_execution_saga(review_id: str) -> DbExecutionSaga:
    async with async_session() as session:
        saga = DbExecutionSaga(
            id=str(uuid.uuid4()), tenant_id=get_tenant_id(),
            review_id=review_id, state="prepared",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )
        session.add(saga)
        try:
            await session.commit()
            return saga
        except IntegrityError:
            await session.rollback()
            result = await session.execute(
                select(DbExecutionSaga).where(DbExecutionSaga.review_id == review_id)
            )
            existing = result.scalar_one()
            raise HTTPException(
                status_code=409,
                detail=f"Execution already has durable state '{existing.state}'; it will not be executed again.",
            )


async def _update_execution_saga(review_id: str, state: str, **fields) -> None:
    values = {"state": state, "updated_at": datetime.now(timezone.utc), **fields}
    async with async_session() as session:
        await session.execute(
            update(DbExecutionSaga)
            .where(DbExecutionSaga.review_id == review_id)
            .values(**values)
        )
        await session.commit()


async def _repair_committed_execution(review_id: str, executed_by: str = "recovery") -> dict:
    """Repair internal records only; this function never executes target SQL."""
    async with async_session() as session:
        result = await session.execute(
            select(DbExecutionSaga).where(DbExecutionSaga.review_id == review_id)
        )
        saga = result.scalar_one_or_none()
        task = await session.scalar(select(DbReviewTask).where(
            DbReviewTask.id == review_id, DbReviewTask.tenant_id == get_tenant_id()))
        if not saga or not task:
            raise LookupError("Execution recovery record not found")
        if saga.state == "records_repaired":
            return {"status": "completed", "review_id": review_id, "replayed": False}
        if saga.state != "target_committed":
            raise ValueError(
                f"Recovery is unsafe from saga state '{saga.state}'; business SQL will not be replayed."
            )
        log_result = await session.execute(
            select(DbOperationLog.id).where(DbOperationLog.review_id == review_id).limit(1)
        )
        has_log = log_result.scalar_one_or_none() is not None
        saga_data = {
            "backup_id": saga.backup_id,
            "affected_rows": saga.affected_rows,
        }
        task_data = {
            "operation_type": task.operation_type, "sql": task.sql,
            "affected_table": task.affected_table or "", "submitted_by": task.submitted_by,
            "approved_by": task.approved_by, "reviewed_at": task.reviewed_at,
            "reviewer_note": task.reviewer_note,
        }

    await _update_review_task_status(
        review_id, "completed", backup_id=saga_data["backup_id"],
        approved_by=task_data["approved_by"], reviewed_at=task_data["reviewed_at"],
        reviewer_note=task_data["reviewer_note"] or "",
    )
    if not has_log:
        await _get_backup_mgr().log_operation(
            operation_type=task_data["operation_type"], sql_text=task_data["sql"],
            affected_rows=saga_data["affected_rows"], backup_id=saga_data["backup_id"],
            status="completed", table_name=task_data["affected_table"],
            executed_by=executed_by, submitted_by=task_data["submitted_by"],
            approved_by=task_data["approved_by"], reviewer_note=task_data["reviewer_note"],
            review_id=review_id,
        )
    await _update_execution_saga(review_id, "records_repaired", error_message=None)
    return {"status": "completed", "review_id": review_id, "replayed": False}


async def repair_pending_executions(limit: int = 100) -> dict:
    """Best-effort outbox worker for target-committed executions."""
    async with async_session() as session:
        result = await session.execute(
            select(DbExecutionSaga.review_id, DbExecutionSaga.tenant_id)
            .where(DbExecutionSaga.state == "target_committed")
            .order_by(DbExecutionSaga.updated_at)
            .limit(limit)
        )
        review_ids = list(result.all())
    repaired, failed = 0, []
    from auth import set_tenant_id, reset_tenant_id
    for review_id, tenant_id in review_ids:
        token = set_tenant_id(tenant_id)
        try:
            await _repair_committed_execution(review_id)
            repaired += 1
        except Exception as exc:
            failed.append({"review_id": review_id, "error": str(exc)[:200]})
        finally:
            reset_tenant_id(token)
    return {"found": len(review_ids), "repaired": repaired, "failed": failed}


async def expire_overdue_reviews() -> int:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            select(DbReviewTask.id, DbReviewTask.tenant_id).where(
                DbReviewTask.status.in_(["awaiting_review", "pending_second_approval", "escalated"]),
                DbReviewTask.expires_at.is_not(None), DbReviewTask.expires_at < now,
            )
        )
        ids = list(result.all())
    from auth import set_tenant_id, reset_tenant_id
    for review_id, tenant_id in ids:
        token = set_tenant_id(tenant_id)
        try:
            await _transition_review_task(
                review_id, ["awaiting_review", "pending_second_approval", "escalated"], "expired"
            )
        finally:
            reset_tenant_id(token)
    return len(ids)


async def _record_approval(
    task_id: str,
    approver_id: str,
    note: str,
    is_first: bool,
) -> None:
    """Persist first/second approver identity, note, and timestamp separately."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            select(DbReviewTask).where(DbReviewTask.id == task_id,
                                       DbReviewTask.tenant_id == get_tenant_id())
        )
        task = result.scalar_one_or_none()
        if not task:
            return
        if is_first:
            task.first_approver_id = approver_id
            task.first_approver_note = note or ""
            task.first_approved_at = now
        else:
            task.second_approver_id = approver_id
            task.second_approver_note = note or ""
            task.second_approved_at = now
        await session.commit()


def _get_backup_mgr() -> BackupManager:
    return BackupManager(create_connector())


def _requires_second_approval(task: dict) -> bool:
    """High-risk operations require two distinct approvers."""
    from config import settings
    if int(task.get("required_approvals") or 1) >= 2:
        return True
    if not settings.four_eyes_enabled:
        return False
    operation_types = {
        item.strip().upper()
        for item in settings.four_eyes_operation_types.split(",")
        if item.strip()
    }
    if task.get("operation_type", "").upper() in operation_types:
        return True
    return int(task.get("affected_rows") or 0) >= settings.four_eyes_affected_rows


# ── Pydantic models ──

class PreviewRequest(BaseModel):
    sql: str

    @field_validator("sql")
    @classmethod
    def sql_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("SQL cannot be empty")
        return v.strip()


class SubmitReviewRequest(BaseModel):
    sql: str
    reason: str = ""


class ApproveRequest(BaseModel):
    reason: str = ""
    reviewer_note: str = ""


class RollbackRequest(BaseModel):
    confirm: bool = False
    reason: str = ""


class ReviewActionRequest(BaseModel):
    assigned_to: str = ""
    reason: str = ""


class BatchReviewRequest(BaseModel):
    review_ids: list[str]
    action: str
    reviewer_note: str = ""

    @field_validator("review_ids")
    @classmethod
    def validate_ids(cls, value):
        if not value or len(value) > 100 or len(set(value)) != len(value):
            raise ValueError("review_ids must contain 1-100 unique IDs")
        return value


# ── Helpers ──

def _extract_table_name(sql: str, operation_type: str) -> str:
    patterns: dict[str, str] = {
        "SELECT": r'\bFROM\s+["`\[]?(\w+)["`\]]?',
        "INSERT": r'\bINTO\s+["`\[]?(\w+)["`\]]?',
        "UPDATE": r'\bUPDATE\s+["`\[]?(\w+)["`\]]?',
        "DELETE": r'\bFROM\s+["`\[]?(\w+)["`\]]?',
    }
    pattern = patterns.get(operation_type)
    if not pattern:
        return "unknown"
    m = re.search(pattern, sql, re.IGNORECASE)
    return m.group(1) if m else "unknown"


_WHERE_INJECTION_RE = re.compile(
    r'(\bUNION\b|\bSELECT\b|--|\bOR\b\s+\d+\s*=\s*\d+|/\*|\*/|;)',
    re.IGNORECASE,
)
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

def _validate_where(where: str) -> None:
    """Reject WHERE clauses that contain injection payloads."""
    if _WHERE_INJECTION_RE.search(where):
        raise HTTPException(status_code=400, detail="WHERE clause contains disallowed SQL patterns")

def _extract_where(sql: str) -> str | None:
    m = re.search(
        r'\bWHERE\b\s+(.+?)(?:\s*(?:ORDER|LIMIT|GROUP|HAVING)\s|;|$)',
        sql, re.IGNORECASE | re.DOTALL,
    )
    where = m.group(1).strip().rstrip(";") if m else None
    if where:
        _validate_where(where)
    return where


def _build_count_sql(sql: str, op_type: str, table_name: str, quoted_table: str | None = None) -> str | None:
    where_clause = _extract_where(sql)
    if not quoted_table:
        return None
    target = quoted_table
    if op_type in ("UPDATE", "DELETE") and where_clause:
        return f'SELECT COUNT(*) as cnt FROM {target} WHERE {where_clause}'
    if op_type == "DELETE" and not where_clause:
        return f'SELECT COUNT(*) as cnt FROM {target}'
    return None


# ═══════════════════════════════════════════════════════════════
# 0a. GET /api/db_operations/status
# ═══════════════════════════════════════════════════════════════

def _categorize_db_error(exc: Exception) -> dict:
    """Classify a database connection error into a structured response."""
    msg = str(exc).lower()
    exc_name = type(exc).__name__

    if isinstance(exc, NotImplementedError) or "not implemented" in msg:
        return {
            "error_type": "not_implemented",
            "message": "当前数据库类型尚未实现支持",
            "suggestion": "请将数据库类型切换为 SQLite，或等待后续版本更新",
        }
    if "unsupported database type" in msg or isinstance(exc, ValueError):
        return {
            "error_type": "unsupported_type",
            "message": "不支持的数据库类型",
            "suggestion": "请在设置中将 db_type 设置为 sqlite",
        }
    if "no module named" in msg:
        return {
            "error_type": "dependency_missing",
            "message": "数据库驱动未安装",
            "suggestion": "请按 backend/requirements.txt 安装对应数据库驱动",
        }
    if "no such file" in msg or "unable to open database" in msg:
        return {
            "error_type": "file_not_found",
            "message": "数据库文件不存在或路径无效",
            "suggestion": "请检查 db_sqlite_path 配置是否正确，确保文件路径可访问",
        }
    if "permission denied" in msg or "access denied" in msg or "readonly" in msg:
        return {
            "error_type": "permission_denied",
            "message": "数据库访问被拒绝",
            "suggestion": "请检查数据库文件的读写权限",
        }
    if "connection refused" in msg or "cannot connect" in msg or "can't connect" in msg or "timeout" in msg:
        return {
            "error_type": "connection_failed",
            "message": "无法连接到数据库",
            "suggestion": "请检查数据库地址、端口是否正确，数据库服务是否在运行",
        }
    if "auth" in msg or "password" in msg or "login" in msg:
        return {
            "error_type": "auth_failed",
            "message": "数据库认证失败",
            "suggestion": "请检查数据库用户名和密码是否正确",
        }
    return {
        "error_type": "unknown",
        "message": "数据库连接失败",
        "suggestion": "请检查数据库配置是否正确，或查看后端日志获取详细信息",
    }


async def _tenant_db_descriptor(user: AuthUser) -> tuple[str, str]:
    """Return only the current tenant's target database metadata."""
    if settings.multi_tenant_enabled:
        from models.schemas import TenantDatabaseConfig
        async with async_session() as session:
            row = await session.scalar(select(TenantDatabaseConfig).where(
                TenantDatabaseConfig.tenant_id == user.tenant_id))
        if row:
            return row.db_type, row.database
        return "unconfigured", ""
    return settings.db_type, settings.db_name or settings.db_sqlite_path


@router.get("/status")
async def get_db_status(user: AuthUser = Depends(get_current_user)):
    """Get target database connection status and detailed info."""
    db_type, db_name = await _tenant_db_descriptor(user)
    try:
        conn = await get_connector()
        tables = await conn.get_tables()
        return {
            "connected": True,
            "tenant_id": user.tenant_id,
            "db_type": db_type,
            "db_name": db_name,
            "table_count": len(tables),
        }
    except Exception as exc:
        logger.warning(f"Database connection check failed: {exc}")
        error_info = _categorize_db_error(exc)
        return {
            "connected": False,
            "tenant_id": user.tenant_id,
            "db_type": db_type,
            "db_name": db_name,
            "table_count": 0,
            "error": error_info,
        }


# ═══════════════════════════════════════════════════════════════
# 0b. POST /api/db_operations/reconnect
# ═══════════════════════════════════════════════════════════════

@router.post("/reconnect")
async def reconnect_db(user: AuthUser = Depends(get_current_user)):
    """Close and reopen the target database connection, then return status."""
    from db_connector.factory import close_tenant_connector, get_connector
    db_type, db_name = await _tenant_db_descriptor(user)

    await close_tenant_connector(user.tenant_id)

    try:
        conn = await get_connector()
        tables = await conn.get_tables()
        logger.info("Database reconnection successful")
        return {
            "connected": True,
            "tenant_id": user.tenant_id,
            "db_type": db_type,
            "db_name": db_name,
            "table_count": len(tables),
        }
    except Exception as exc:
        logger.warning(f"Database reconnection failed: {exc}")
        error_info = _categorize_db_error(exc)
        return {
            "connected": False,
            "tenant_id": user.tenant_id,
            "db_type": db_type,
            "db_name": db_name,
            "table_count": 0,
            "error": error_info,
        }


# ═══════════════════════════════════════════════════════════════
# 0c. GET /api/db_operations/tables/{table_name}/data
# ═══════════════════════════════════════════════════════════════

_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


async def _require_table(conn, table_name: str) -> str:
    """Validate a path-supplied table name against the live schema whitelist."""
    if not _TABLE_NAME_RE.match(table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")
    tables = await conn.get_tables()
    if table_name not in tables:
        raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
    return conn.quote_identifier(table_name)


async def _quote_table_if_known(conn, table_name: str) -> str | None:
    """Return a quoted table name only when it is a known, safe table."""
    if not table_name or table_name == "unknown" or not _TABLE_NAME_RE.match(table_name):
        return None
    try:
        tables = await conn.get_tables()
    except Exception:
        return None
    if table_name not in tables:
        return None
    return conn.quote_identifier(table_name)

@router.get("/tables/{table_name}/data")
async def get_table_data(
    table_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    sort: str = Query(""),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    user: AuthUser = Depends(get_current_user),
):
    """Get paginated data from a table with column-whitelisted sorting."""
    if not _TABLE_NAME_RE.match(table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")

    conn = await get_connector()
    quoted_table = await _require_table(conn, table_name)

    # Get schema for column whitelist
    try:
        schema = await conn.get_schema(table_name)
    except Exception:
        logger.error(f"Schema read failed for {table_name}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to read table schema.")

    if not schema or not schema.get("columns"):
        raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")

    columns = schema["columns"]
    valid_columns = {c["name"] for c in columns}

    # Validate sort column against whitelist
    order_clause = ""
    if sort:
        if sort not in valid_columns:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort column: {sort}. Valid columns: {', '.join(sorted(valid_columns))}",
            )
        direction = "DESC" if order == "desc" else "ASC"
        order_clause = f' ORDER BY {conn.quote_identifier(sort)} {direction}'

    # Count total rows
    try:
        count_result = await conn.query(f'SELECT COUNT(*) as cnt FROM {quoted_table}')
        total = count_result[0]["cnt"] if count_result else 0
    except Exception:
        logger.error(f"Count failed for {table_name}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to count table rows.")

    # Fetch page
    offset = (page - 1) * page_size
    try:
        sql = f'SELECT * FROM {quoted_table}{order_clause} LIMIT {page_size} OFFSET {offset}'
        rows = await conn.query(sql)
    except Exception:
        logger.error(f"Data query failed for {table_name}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to read table data.")

    col_names = list(rows[0].keys()) if rows else [c["name"] for c in columns]
    data_rows = [list(r.values()) for r in rows]

    return {
        "columns": col_names,
        "rows": mask_rows(col_names, data_rows),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ═══════════════════════════════════════════════════════════════
# 1. GET /api/db_operations/tables
# ═══════════════════════════════════════════════════════════════

@router.get("/tables")
async def list_tables(user: AuthUser = Depends(get_current_user)):
    """List all tables in the target database with column info and row counts."""
    try:
        conn = await get_connector()
        tables = await conn.get_tables()
        result = []
        for t in tables:
            try:
                schema = await conn.get_schema(t)
                quoted_t = conn.quote_identifier(t)
                count_rows = await conn.query(f'SELECT COUNT(*) as cnt FROM {quoted_t}')
                row_count = count_rows[0]["cnt"] if count_rows else 0
                result.append({
                    "name": t,
                    "columns": schema.get("columns", []),
                    "row_count": row_count,
                })
            except Exception:
                logger.error(f"Failed to read table {t}:\n{traceback.format_exc()}")
                result.append({"name": t, "columns": [], "row_count": 0})
        return result
    except Exception:
        logger.error(f"Failed to list tables:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to read database tables. Check your database connection settings.")


# ═══════════════════════════════════════════════════════════════
# 2. GET /api/db_operations/tables/{table_name}
# ═══════════════════════════════════════════════════════════════

@router.get("/tables/{table_name}")
async def get_table_schema(table_name: str, user: AuthUser = Depends(get_current_user)):
    """Get full schema and row count for a specific table."""
    try:
        conn = await get_connector()
        quoted_table = await _require_table(conn, table_name)
        schema = await conn.get_schema(table_name)
        if not schema or not schema.get("columns"):
            raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")
        count_rows = await conn.query(f'SELECT COUNT(*) as cnt FROM {quoted_table}')
        schema["row_count"] = count_rows[0]["cnt"] if count_rows else 0
        return schema
    except HTTPException:
        raise
    except Exception:
        logger.error(f"Failed to get schema for table {table_name}:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to read table schema.")


# ═══════════════════════════════════════════════════════════════
# 3. POST /api/db_operations/preview
# ═══════════════════════════════════════════════════════════════

@router.post("/preview")
async def preview_operation(
    req: PreviewRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Submit SQL for safety classification and data preview."""
    conn = await get_connector()

    # 1. Gather table row counts for full-scan detection
    table_row_counts: dict[str, int] = {}
    try:
        for t in await conn.get_tables():
            try:
                r = await conn.query(f'SELECT COUNT(*) as cnt FROM {conn.quote_identifier(t)}')
                table_row_counts[t] = r[0]["cnt"] if r else 0
            except Exception:
                pass
    except Exception:
        pass

    # 2. Safety check
    safety = _safety.check(req.sql, table_row_counts)
    if safety.blocked:
        raise HTTPException(status_code=400, detail=safety.message)

    # 3. Parse operation type
    first_word = req.sql.strip().upper().split()[0]
    valid_ops = {"SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE"}
    operation_type = first_word if first_word in valid_ops else "UNKNOWN"
    affected_table = _extract_table_name(req.sql, operation_type)
    quoted_affected = await _quote_table_if_known(conn, affected_table)

    columns: list[str] = []
    preview_rows: list[list] = []
    affected_rows = 0

    # 4. Execute preview
    if operation_type == "SELECT":
        try:
            limited_sql = req.sql.rstrip(";").strip()
            if "LIMIT" not in limited_sql.upper():
                limited_sql = f"{limited_sql} LIMIT 500"
            rows = await conn.query(limited_sql)
            if rows:
                columns = list(rows[0].keys())
                preview_rows = mask_rows(columns, [list(r.values()) for r in rows[:10]])
                affected_rows = len(rows)
            safety.warnings.extend(_safety.check_result_size(affected_rows))
        except Exception:
            logger.error(f"SQL preview execution error:\n{traceback.format_exc()}")
            raise HTTPException(status_code=400, detail="SQL execution failed. Please check the syntax and try again.")

    elif operation_type in ("INSERT", "UPDATE", "DELETE"):
        count_sql = _build_count_sql(
            req.sql, operation_type, affected_table, quoted_affected
        )
        if count_sql:
            try:
                r = await conn.query(count_sql)
                affected_rows = r[0]["cnt"] if r else 0
            except Exception:
                pass

        if affected_rows > 0 and operation_type in ("UPDATE", "DELETE"):
            where_clause = _extract_where(req.sql)
            if where_clause:
                try:
                    preview = await conn.query(
                        f'SELECT * FROM {quoted_affected} WHERE {where_clause} LIMIT 10'
                    )
                    if preview:
                        columns = list(preview[0].keys())
                        preview_rows = mask_rows(columns, [list(r.values()) for r in preview])
                except Exception:
                    pass

    return {
        "operation_type": operation_type,
        "sql": req.sql,
        "affected_table": affected_table,
        "affected_rows": affected_rows,
        "columns": columns,
        "preview_rows": preview_rows,
        "has_backup": operation_type in ("INSERT", "UPDATE", "DELETE") and affected_rows > 0,
        "backup_id": None,
        "safety_level": safety.level,
        "safety_message": safety.message,
        "warnings": safety.warnings,
    }


# ═══════════════════════════════════════════════════════════════
# 4. POST /api/db_operations/submit-review
# ═══════════════════════════════════════════════════════════════

def _calculate_risk_score(operation_type: str, sql: str, affected_rows: int,
                          policy_conflicts: list | None = None) -> tuple[int, list[str]]:
    score = {"INSERT": 15, "UPDATE": 35, "DELETE": 55}.get(operation_type, 10)
    factors = [f"operation:{operation_type.lower()}"]
    if affected_rows >= 1000:
        score += 30; factors.append("large_change:1000+")
    elif affected_rows >= 100:
        score += 20; factors.append("large_change:100+")
    elif affected_rows >= 10:
        score += 10; factors.append("multi_row_change")
    if re.search(r"\b(password|secret|token|api_key|credit_card|phone|email)\b", sql, re.I):
        score += 20; factors.append("sensitive_column")
    if operation_type in {"UPDATE", "DELETE"} and not _extract_where(sql):
        score += 40; factors.append("missing_where")
    if policy_conflicts:
        score += 10; factors.append("policy_conflict")
    return min(score, 100), factors

@router.post("/submit-review")
async def submit_review(
    req: SubmitReviewRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: AuthUser = Depends(require_roles("operator", "approver")),
):
    """Submit a write operation into the review queue."""
    from organization_context import get_organization_context
    organization_context = get_organization_context()
    organization_id = (organization_context.organization_id
                       if organization_context else user.tenant_id)
    membership_id = organization_context.membership_id if organization_context else None
    scoped_idempotency_key = ((f"{organization_id}|{idempotency_key}"
                               if organization_context else idempotency_key)
                              if idempotency_key else None)
    if scoped_idempotency_key:
        if len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="Idempotency-Key is too long")
        async with async_session() as session:
            result = await session.execute(
                select(DbReviewTask).where(
                    DbReviewTask.organization_id == organization_id,
                    DbReviewTask.idempotency_key == scoped_idempotency_key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                if existing.sql != req.sql or (existing.reason or "") != req.reason:
                    raise HTTPException(status_code=409, detail="Idempotency-Key was already used for another request")
                return _task_to_dict(existing)

    conn = await get_connector()

    safety = _safety.check(req.sql)
    if safety.blocked:
        raise HTTPException(status_code=400, detail=safety.message)

    first_word = req.sql.strip().upper().split()[0]
    if first_word not in ("INSERT", "UPDATE", "DELETE"):
        raise HTTPException(
            status_code=400,
            detail=f"Review only accepts write operations. Got: {first_word}",
        )

    affected_table = _extract_table_name(req.sql, first_word)
    quoted_affected = await _quote_table_if_known(conn, affected_table)
    affected_rows = 0
    preview_columns: list[str] = []
    preview_rows: list[list] = []

    count_sql = _build_count_sql(req.sql, first_word, affected_table, quoted_affected)
    if count_sql:
        try:
            r = await conn.query(count_sql)
            affected_rows = r[0]["cnt"] if r else 0
        except Exception:
            pass

    if affected_rows > 0 and first_word in ("UPDATE", "DELETE"):
        where_clause = _extract_where(req.sql)
        if where_clause:
            try:
                preview = await conn.query(
                    f'SELECT * FROM {quoted_affected} WHERE {where_clause} LIMIT 10'
                )
                if preview:
                    preview_columns = list(preview[0].keys())
                    preview_rows = mask_rows(preview_columns, [list(r.values()) for r in preview[:10]])
            except Exception:
                pass

    from approval_policy import evaluate_policy
    policy_result = await evaluate_policy(
        first_word, affected_table, affected_rows, req.sql
    )
    selected_policy = policy_result["policy"]
    risk_score, risk_factors = _calculate_risk_score(
        first_word, req.sql, affected_rows, policy_result["conflicts"]
    )

    try:
        task = await _create_review_task(
            sql=req.sql, reason=req.reason, operation_type=first_word,
            affected_table=affected_table, affected_rows=affected_rows,
            preview_columns=preview_columns, preview_rows=preview_rows,
            submitted_by=user.id,
            idempotency_key=scoped_idempotency_key,
            policy_id=selected_policy.id if selected_policy else None,
            policy_version=selected_policy.version if selected_policy else None,
            required_approvals=policy_result["required_approvals"],
            risk_score=risk_score, risk_factors=risk_factors,
            tenant_id=user.tenant_id,
            organization_id=organization_id,
            membership_id=membership_id,
        )
    except IntegrityError:
        # The unique key is the synchronization primitive. Concurrent requests
        # race on INSERT, then the loser returns the committed winner.
        if not scoped_idempotency_key:
            raise
        async with async_session() as session:
            result = await session.execute(
                select(DbReviewTask).where(
                    DbReviewTask.organization_id == organization_id,
                    DbReviewTask.idempotency_key == scoped_idempotency_key)
            )
            existing = result.scalar_one_or_none()
        if not existing:
            raise
        if existing.sql != req.sql or (existing.reason or "") != req.reason:
            raise HTTPException(status_code=409, detail="Idempotency-Key was already used for another request")
        return _task_to_dict(existing)

    task["policy_matches"] = policy_result["matches"]
    task["policy_conflicts"] = policy_result["conflicts"]
    task["policy_reason"] = policy_result["reason"]

    try:
        mgr = _get_backup_mgr()
        await mgr.log_operation(
            operation_type=first_word, sql_text=req.sql,
            affected_rows=affected_rows, backup_id=None,
            status="awaiting_review", table_name=affected_table,
            executed_by=user.username,
            submitted_by=user.id,
        )
    except Exception:
        pass

    return task


# ═══════════════════════════════════════════════════════════════
# 4b. GET /api/db_operations/reviews
# ═══════════════════════════════════════════════════════════════

@router.get("/reviews")
async def list_reviews(
    status: str = Query(""),
    user: AuthUser = Depends(require_roles("approver")),
):
    """List review tasks, optionally filtered by comma-separated statuses."""
    statuses = [s.strip() for s in status.split(",") if s.strip()]

    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        query = select(DbReviewTask).where(DbReviewTask.tenant_id.in_(scope_ids))
        if statuses:
            query = query.where(DbReviewTask.status.in_(statuses))
        query = query.order_by(DbReviewTask.created_at.desc()).limit(200)
        result = await session.execute(query)
        tasks = result.scalars().all()

    return {"items": [_task_to_dict(t) for t in tasks], "total": len(tasks)}


# ═══════════════════════════════════════════════════════════════
# 5. GET /api/db_operations/review/{review_id}
# ═══════════════════════════════════════════════════════════════

@router.get("/review/{review_id}")
async def get_review(
    review_id: str,
    user: AuthUser = Depends(require_roles("approver")),
):
    """Get a review task by ID."""
    task = await _get_review_task(review_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found or expired")
    return task


@router.post("/review/{review_id}/actions/{action}")
async def review_state_action(
    review_id: str, action: str, body: ReviewActionRequest = ReviewActionRequest(),
    user: AuthUser = Depends(require_roles("operator", "approver", "admin")),
):
    """Expire, revoke, escalate, or transfer an approval task."""
    task = await _get_review_task(review_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found")
    active = ["awaiting_review", "pending_second_approval", "escalated"]
    if action in {"expire", "escalate", "transfer"} and user.role not in {"approver", "admin"}:
        raise HTTPException(status_code=403, detail="Only approvers may manage review routing")
    if action == "expire":
        changed = await _transition_review_task(review_id, active, "expired")
    elif action == "revoke":
        if task.get("submitted_by") and task["submitted_by"] != user.id and user.role != "admin":
            raise HTTPException(status_code=403, detail="Only the submitter or an admin may revoke")
        changed = await _transition_review_task(review_id, active, "revoked")
    elif action == "escalate":
        changed = await _transition_review_task(
            review_id, ["awaiting_review", "pending_second_approval"], "escalated",
            assigned_to=body.assigned_to or None,
        )
    elif action == "transfer":
        if not body.assigned_to:
            raise HTTPException(status_code=400, detail="assigned_to is required")
        if settings.multi_tenant_enabled:
            async with async_session() as session:
                assignee = await session.scalar(select(Membership).where(
                    Membership.tenant_id == user.tenant_id,
                    Membership.user_id == body.assigned_to,
                    Membership.role.in_(["approver", "admin"]), Membership.active.is_(True)))
            if not assignee:
                raise HTTPException(status_code=422, detail="Assigned approver is not an active tenant member")
        changed = await _transition_review_task(
            review_id, active, task["status"], assigned_to=body.assigned_to,
        )
        if changed:
            await _emit_domain_event(review_id, "review.transferred", {
                "assigned_to": body.assigned_to, "reason": body.reason,
            })
    else:
        raise HTTPException(status_code=404, detail="Unsupported review action")
    if not changed:
        raise HTTPException(status_code=409, detail="Illegal review state transition")
    return await _get_review_task(review_id)


@router.get("/review/{review_id}/events")
async def review_events(review_id: str, user: AuthUser = Depends(require_roles("approver"))):
    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(DomainEvent).where(DomainEvent.aggregate_id == review_id,
                                      DomainEvent.tenant_id.in_(scope_ids))
            .order_by(DomainEvent.created_at)
        )).scalars().all()
    return {"items": [{"event_type": row.event_type, "payload": row.payload,
                       "created_at": row.created_at.isoformat()} for row in rows]}


@router.get("/review/{review_id}/execution-result")
async def review_execution_result(review_id: str, user: AuthUser = Depends(require_roles("approver"))):
    task = await _get_review_task(review_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found")
    if not task.get("execution_result"):
        raise HTTPException(status_code=409, detail="Review has not produced an execution result")
    return task["execution_result"]


async def _capture_after_rows(conn, table_name: str, snapshot: dict | None) -> tuple[list[str], list[list]]:
    if not snapshot or not snapshot.get("rows") or not snapshot.get("primary_keys"):
        return [], []
    columns, rows, keys = snapshot["columns"], snapshot["rows"][:10], snapshot["primary_keys"]
    indexes = {name: index for index, name in enumerate(columns)}
    values, groups = [], []
    named_values = {}
    postgres = conn.__class__.__name__ == "PostgreSQLConnector"
    sqlite = conn.__class__.__name__ == "SqliteConnector"
    for row in rows:
        terms = []
        for key in keys:
            value = row[indexes[key]]
            values.append(value)
            if sqlite:
                name = f"p{len(values)}"
                named_values[name] = value
                placeholder = f":{name}"
            else:
                placeholder = f"${len(values)}" if postgres else conn.placeholder()
            terms.append(f"{conn.quote_identifier(key)} = {placeholder}")
        groups.append("(" + " AND ".join(terms) + ")")
    found = await conn.query(
        f"SELECT * FROM {conn.quote_identifier(table_name)} WHERE " + " OR ".join(groups),
        named_values if sqlite else tuple(values),
    )
    if not found:
        return columns, []
    result_columns = list(found[0].keys())
    return result_columns, mask_rows(result_columns, [list(row.values()) for row in found])


@router.post("/reviews/batch")
async def batch_review(body: BatchReviewRequest, request: Request,
                       user: AuthUser = Depends(require_roles("approver"))):
    if body.action not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="action must be approve or reject")
    results = []
    for review_id in body.review_ids:
        try:
            if body.action == "approve":
                result = await approve_review(
                    review_id, request, ApproveRequest(reviewer_note=body.reviewer_note), user
                )
            else:
                result = await reject_review(
                    review_id, ApproveRequest(reviewer_note=body.reviewer_note), user
                )
            results.append({"review_id": review_id, "ok": True, "result": result})
        except HTTPException as exc:
            results.append({"review_id": review_id, "ok": False,
                            "status_code": exc.status_code, "error": exc.detail})
    return {"action": body.action, "total": len(results),
            "succeeded": sum(item["ok"] for item in results), "items": results}


# ═══════════════════════════════════════════════════════════════
# 6. POST /api/db_operations/review/{review_id}/approve
# ═══════════════════════════════════════════════════════════════

@router.post("/review/{review_id}/approve")
async def approve_review(
    review_id: str,
    request: Request,
    req: ApproveRequest = ApproveRequest(),
    user: AuthUser = Depends(require_roles("approver")),
):
    """Approve a review — backup then execute the SQL."""
    task = await _get_review_task(review_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found or expired")
    if task["status"] not in ("awaiting_review", "pending_second_approval", "escalated"):
        raise HTTPException(
            status_code=409,
            detail=f"Task status is '{task['status']}', cannot approve",
        )
    if task.get("assigned_to") and task["assigned_to"] != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Review is assigned to another approver")

    if auth_enabled(request):
        if task.get("submitted_by") and task["submitted_by"] == user.id:
            raise HTTPException(
                status_code=403,
                detail="Self-approval is not allowed. A different approver is required.",
            )

        if _requires_second_approval(task):
            existing = task.get("approved_by") or ""
            if task["status"] in ("awaiting_review", "escalated") and not existing:
                now = datetime.now(timezone.utc)
                claimed = await _transition_review_task(
                    review_id,
                    ["awaiting_review", "escalated"],
                    "pending_second_approval",
                    approved_by=user.id,
                    reviewed_at=now,
                    reviewer_note=req.reviewer_note,
                )
                if not claimed:
                    raise HTTPException(status_code=409, detail="Review task state changed, please refresh.")
                await _record_approval(
                    review_id,
                    approver_id=user.id,
                    note=req.reviewer_note,
                    is_first=True,
                )
                return {
                    "status": "pending_second_approval",
                    "review_id": review_id,
                    "message": "This high-risk operation requires a second approver.",
                }
            if existing == user.id:
                raise HTTPException(
                    status_code=409,
                    detail="This operation still requires a different second approver.",
                )

    claimed = await _transition_review_task(
        review_id,
        ["awaiting_review", "pending_second_approval", "escalated"],
        "executing",
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="Review task is already being processed.")

    conn = await get_connector()
    mgr = _get_backup_mgr()
    await _create_execution_saga(review_id)

    backup_id = None
    before_snapshot = None
    target_committed = False
    try:
        await _record_approval(
            review_id,
            approver_id=user.id,
            note=req.reviewer_note,
            is_first=task["status"] != "pending_second_approval",
        )
        # Snapshot locking and the business write share one target transaction.
        async with conn.transaction() as tx:
            if task["affected_rows"] > 0:
                where_clause = _extract_where(task["sql"])
                if where_clause:
                    backup_result = await mgr.create_backup(
                        table_name=task["affected_table"],
                        condition=where_clause,
                        operation_type=task["operation_type"],
                        transaction=tx,
                    )
                    backup_id = backup_result.backup_id
                    before_snapshot = backup_result.data_snapshot
                    await _update_execution_saga(review_id, "backup_ready", backup_id=backup_id)
            affected = await tx.execute(task["sql"])
        target_committed = True
        actual_rows = affected if affected >= 0 else task["affected_rows"]
        after_columns, after_rows = await _capture_after_rows(
            conn, task["affected_table"], before_snapshot
        )
        execution_result = {
            "review_id": review_id, "operation_type": task["operation_type"],
            "affected_rows": actual_rows, "backup_id": backup_id,
            "before": {"columns": task["preview_columns"], "rows": task["preview_rows"]},
            "after": {"columns": after_columns, "rows": after_rows},
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

        # This durable marker is the outbox boundary. Recovery repairs records;
        # it never replays the business SQL.
        await _update_execution_saga(
            review_id, "target_committed", backup_id=backup_id, affected_rows=actual_rows
        )

        await _update_review_task_status(
            review_id, "completed", backup_id=backup_id, approved_by=user.id,
            reviewed_at=datetime.now(timezone.utc), reviewer_note=req.reviewer_note,
        )
        async with async_session() as session:
            from organization_context import get_visible_organization_ids
            scope_ids = await get_visible_organization_ids(user.tenant_id)
            stored_task = await session.scalar(select(DbReviewTask).where(
                DbReviewTask.id == review_id, DbReviewTask.tenant_id.in_(scope_ids)))
            stored_task.execution_result = execution_result
            await session.commit()
        await mgr.log_operation(
            operation_type=task["operation_type"], sql_text=task["sql"],
            affected_rows=actual_rows, backup_id=backup_id,
            status="completed", table_name=task["affected_table"],
            executed_by=user.username, submitted_by=task.get("submitted_by"),
            approved_by=user.id, reviewer_note=req.reviewer_note,
            review_id=review_id,
        )
        await _update_execution_saga(review_id, "records_repaired")
        return {"status": "completed", "affected_rows": actual_rows, "backup_id": backup_id,
                "execution_result": execution_result}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"SQL execution/reconciliation failed for review {review_id}:\n{traceback.format_exc()}")
        if target_committed:
            # Never tell callers the target was unchanged after commit. Keep the
            # task non-retryable and expose an explicit repair state.
            try:
                await _update_execution_saga(
                    review_id, "target_committed", backup_id=backup_id,
                    affected_rows=locals().get("actual_rows", 0),
                    error_message="Internal records require repair",
                )
                await _update_review_task_status(review_id, "executed_record_pending", backup_id=backup_id)
            except Exception:
                logger.critical("Could not persist post-commit repair state for review %s", review_id)
            return {
                "status": "executed_record_pending",
                "affected_rows": locals().get("actual_rows", 0),
                "backup_id": backup_id,
                "message": "Business SQL committed; internal records are pending repair. Do not retry.",
            }

        try:
            await _update_execution_saga(review_id, "target_rolled_back", error_message=str(exc)[:500])
            await _update_review_task_status(review_id, "failed")
        except Exception:
            logger.critical("Could not persist rollback state for review %s", review_id)
        raise HTTPException(
            status_code=500,
            detail="SQL execution failed before target commit; the target transaction was rolled back.",
        )


# ═══════════════════════════════════════════════════════════════
# 7. POST /api/db_operations/review/{review_id}/reject
# ═══════════════════════════════════════════════════════════════

@router.post("/review/{review_id}/reject")
async def reject_review(
    review_id: str,
    req: ApproveRequest = ApproveRequest(),
    user: AuthUser = Depends(require_roles("approver")),
):
    """Reject a review — operation discarded, no data changed."""
    task = await _get_review_task(review_id)
    if not task:
        raise HTTPException(status_code=404, detail="Review task not found or expired")
    if task["status"] not in ("awaiting_review", "pending", "pending_second_approval", "escalated"):
        raise HTTPException(
            status_code=409,
            detail=f"Task status is '{task['status']}', cannot reject",
        )
    if task.get("assigned_to") and task["assigned_to"] != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Review is assigned to another approver")

    rejected = await _transition_review_task(
        review_id,
        ["awaiting_review", "pending", "pending_second_approval", "escalated"],
        "rejected",
        approved_by=user.id, reviewer_note=req.reviewer_note,
        reviewed_at=datetime.now(timezone.utc),
    )
    if not rejected:
        raise HTTPException(status_code=409, detail="Review task state changed, please refresh.")

    try:
        mgr = _get_backup_mgr()
        await mgr.log_operation(
            operation_type=task["operation_type"], sql_text=task["sql"],
            affected_rows=0, backup_id=None,
            status="rejected", table_name=task["affected_table"],
            executed_by=user.username,
            submitted_by=task.get("submitted_by"),
            approved_by=user.id, reviewer_note=req.reviewer_note,
            review_id=review_id,
        )
    except Exception:
        pass

    return {"status": "rejected"}


# ═══════════════════════════════════════════════════════════════
# 8. POST /api/db_operations/rollback/{backup_id}
# ═══════════════════════════════════════════════════════════════

@router.post("/rollback/{backup_id}")
async def rollback_operation(
    backup_id: str,
    body: RollbackRequest = RollbackRequest(),
    user: AuthUser = Depends(require_roles("approver")),
):
    """Rollback to a previous backup point."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Rollback requires explicit confirmation.")

    reverse_backup_id = None
    rollback_record_id = str(uuid.uuid4())
    original_review_id = None
    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        backup = await session.scalar(select(DbBackup).where(
            DbBackup.id == backup_id, DbBackup.tenant_id.in_(scope_ids)))
        if backup:
            await get_connector()
            mgr = _get_backup_mgr()
            original_review_id = await session.scalar(
                select(DbReviewTask.id).where(DbReviewTask.backup_id == backup_id).limit(1)
            )
            try:
                reverse = await mgr.create_backup(
                    table_name=backup.table_name,
                    condition=backup.condition_sql,
                    operation_type="ROLLBACK",
                )
                reverse_backup_id = reverse.backup_id
            except Exception:
                logger.error(
                    f"Reverse backup before rollback failed for {backup_id}:\n"
                    f"{traceback.format_exc()}"
                )
                # A rollback without a recoverable pre-rollback snapshot is
                # forbidden. Do not call mgr.rollback() after this point.
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Failed to create the reverse backup. Rollback was not started "
                        "and the target database was not modified."
                    ),
                )
            session.add(DbRollbackRecord(
                id=rollback_record_id, tenant_id=user.tenant_id,
                original_review_id=original_review_id,
                original_backup_id=backup_id, reverse_backup_id=reverse_backup_id,
                reason=body.reason, status="executing",
            ))
            await session.commit()

    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    try:
        restored = await mgr.rollback(backup_id)
    except ValueError as e:
        async with async_session() as session:
            record = await session.get(DbRollbackRecord, rollback_record_id)
            if record:
                record.status = "failed"
                record.error_message = str(e)
                await session.commit()
        raise HTTPException(status_code=404, detail=str(e))

    audit_log_id = await mgr.log_operation(
        operation_type="ROLLBACK",
        sql_text=f"ROLLBACK TO BACKUP {backup_id}",
        affected_rows=restored,
        backup_id=backup_id,
        status="completed",
        executed_by=user.username,
    )
    async with async_session() as session:
        record = await session.get(DbRollbackRecord, rollback_record_id)
        if record:
            record.status = "completed"
            record.audit_log_id = audit_log_id
            await session.commit()
    return {
        "backup_id": backup_id,
        "reverse_backup_id": reverse_backup_id,
        "status": "rolled_back",
        "restored_rows": restored,
        "rollback_record_id": rollback_record_id,
        "original_review_id": original_review_id,
    }


# ═══════════════════════════════════════════════════════════════
# 9. GET /api/db_operations/logs
# ═══════════════════════════════════════════════════════════════

@router.get("/logs")
async def get_operation_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    operation_type: str = Query(""),
    table_name: str = Query(""),
    status: str = Query(""),
    user: AuthUser = Depends(require_roles("approver")),
):
    """Get operation audit logs with pagination and optional filters."""
    from organization_context import get_visible_organization_ids
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        query = select(DbOperationLog).where(DbOperationLog.tenant_id.in_(scope_ids))

        if operation_type:
            query = query.where(DbOperationLog.operation_type == operation_type)
        if table_name:
            query = query.where(DbOperationLog.table_name == table_name)
        if status:
            query = query.where(DbOperationLog.status == status)

        # Count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        # Paginate
        offset = (page - 1) * page_size
        query = query.order_by(DbOperationLog.created_at.desc()).offset(offset).limit(page_size)
        result = await session.execute(query)
        logs = result.scalars().all()

        items = [
            {
                "id": log.id,
                "operation_type": log.operation_type,
                "sql_text": log.sql_text,
                "affected_rows": log.affected_rows,
                "table_name": log.table_name or "",
                "backup_id": log.backup_id,
                "status": log.status.value if hasattr(log.status, "value") else str(log.status),
                "error_message": log.error_message,
                "executed_by": log.executed_by or "agent",
                "submitted_by": log.submitted_by or "",
                "approved_by": log.approved_by or "",
                "created_at": log.created_at.isoformat() if log.created_at else "",
            }
            for log in logs
        ]

        return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/audit/verify")
async def verify_operation_audit(
    after_sequence: int = Query(0, ge=0),
    user: AuthUser = Depends(require_roles("approver")),
):
    """Run full or incremental audit hash-chain verification."""
    report = await verify_audit_chain(after_sequence)
    if not report.valid:
        logger.critical("Audit chain verification failed: %s", report.errors)
    return {"valid": report.valid, "checked": report.checked, "errors": report.errors}


@router.post("/audit/recover/{review_id}")
async def recover_execution_records(
    review_id: str,
    user: AuthUser = Depends(require_roles("admin")),
):
    """Repair internal records for a committed target operation without replaying SQL."""
    try:
        return await _repair_committed_execution(review_id, user.username)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/audit/recovery-pending")
async def list_pending_execution_repairs(
    user: AuthUser = Depends(require_roles("admin")),
):
    """List non-terminal saga rows; none of these may be replayed automatically."""
    async with async_session() as session:
        result = await session.execute(
            select(DbExecutionSaga)
            .where(DbExecutionSaga.state != "records_repaired")
            .order_by(DbExecutionSaga.updated_at)
            .limit(200)
        )
        rows = result.scalars().all()
    return {"items": [{
        "review_id": row.review_id, "state": row.state,
        "backup_id": row.backup_id, "affected_rows": row.affected_rows,
        "error_message": row.error_message or "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    } for row in rows], "total": len(rows)}
