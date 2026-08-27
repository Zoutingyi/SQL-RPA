import asyncio
import logging
import os
import traceback
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from config import settings
from models.database import init_db
from datetime import datetime, timedelta, timezone
from models.database import async_session
from models.schemas import Document, DocStatus
from sqlalchemy import select, update

logger = logging.getLogger("sql_rpa")

_backup_cleanup_task: asyncio.Task | None = None
_saga_recovery_task: asyncio.Task | None = None


async def _backup_cleanup_loop():
    """Periodically clean up expired database backups (every 6 hours)."""
    while True:
        await asyncio.sleep(6 * 3600)  # 6 hours
        try:
            from db_connector.backup import BackupManager
            mgr = BackupManager(None)
            count = await mgr.cleanup_expired_backups()
            if count > 0:
                logger.info(f"Backup cleanup: {count} expired backups marked as expired")
            from audit import verify_audit_chain
            report = await verify_audit_chain()
            if not report.valid:
                logger.critical("AUDIT CHAIN ALERT: %s", report.errors)
        except Exception:
            logger.error(f"Backup cleanup failed:\n{traceback.format_exc()}")


async def _saga_recovery_loop():
    """Repair committed target operations' internal records without SQL replay."""
    while True:
        try:
            from api.db_operations import repair_pending_executions
            result = await repair_pending_executions()
            if result["repaired"]:
                logger.warning("Saga recovery repaired %s execution record(s)", result["repaired"])
            if result["failed"]:
                logger.error("Saga recovery failures: %s", result["failed"])
            from api.db_operations import expire_overdue_reviews
            expired = await expire_overdue_reviews()
            if expired:
                logger.info("Expired %s overdue review task(s)", expired)
            from notifications import deliver_pending
            await deliver_pending()
        except Exception:
            logger.error(f"Saga recovery worker failed:\n{traceback.format_exc()}")
        await asyncio.sleep(60)


async def _cleanup_stuck_documents():
    """Mark documents stuck in intermediate states > 30 min as failed."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    stuck_statuses = (DocStatus.parsing, DocStatus.chunking, DocStatus.embedding, DocStatus.indexing)
    async with async_session() as session:
        result = await session.execute(
            select(Document.id, Document.filename)
            .where(Document.status.in_(stuck_statuses))
            .where(Document.updated_at < cutoff)
        )
        stuck = result.fetchall()
        if stuck:
            await session.execute(
                update(Document)
                .where(Document.id.in_([r[0] for r in stuck]))
                .values(status=DocStatus.failed, error_message="入库超时未完成，自动标记为失败")
            )
            await session.commit()
            for doc_id, filename in stuck:
                print(f"[startup] Stuck document marked failed: {filename} ({doc_id})", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _backup_cleanup_task, _saga_recovery_task
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    from migration_gate import check_database_revision
    revision = await check_database_revision()
    app.state.db_revision_ready = revision.ready
    app.state.db_revision = revision.current
    app.state.db_revision_expected = revision.expected
    if not revision.ready:
        logger.critical("Database revision mismatch: current=%s expected=%s error=%s",
                        revision.current, revision.expected, revision.error)
        yield
        return
    await init_db()
    from auth import ensure_default_tenant
    await ensure_default_tenant()
    from prompt_registry import register_active_prompt
    await register_active_prompt()
    from auth import ensure_default_admin
    await ensure_default_admin()
    await _cleanup_stuck_documents()
    from reranker.factory import preload_reranker_async
    await preload_reranker_async()
    from ocr.factory import preload_ocr_async
    await preload_ocr_async()

    # ── Startup: verify target database connectivity ──
    try:
        from db_connector.factory import get_connector
        conn = await get_connector()
        tables = await conn.get_tables()
        db_name = settings.db_name or settings.db_sqlite_path
        logger.info(f"Target database connected: {settings.db_type}://{db_name}, {len(tables)} tables")
    except Exception as exc:
        db_name = settings.db_name or settings.db_sqlite_path
        logger.warning(f"Target database connection failed ({settings.db_type}://{db_name}): {exc}")

    # ── Start background cleanup task ──
    _backup_cleanup_task = asyncio.create_task(_backup_cleanup_loop())
    _saga_recovery_task = asyncio.create_task(_saga_recovery_loop())

    yield

    # ── Shutdown ──
    if _backup_cleanup_task:
        _backup_cleanup_task.cancel()
        try:
            await _backup_cleanup_task
        except asyncio.CancelledError:
            pass
    if _saga_recovery_task:
        _saga_recovery_task.cancel()
        try:
            await _saga_recovery_task
        except asyncio.CancelledError:
            pass
    from db_connector.factory import close_connector
    await close_connector()


app = FastAPI(title="SQL-RPA Agent", lifespan=lifespan)
app.state.db_revision_ready = False

from fastapi.openapi.utils import get_openapi


def _versioned_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version="user-api-v1", routes=app.routes)
    schema["info"]["x-api-contract-version"] = "user-api-v1"
    app.openapi_schema = schema
    return schema


app.openapi = _versioned_openapi

from middleware.logging import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Content-Type", "Authorization", "X-Request-ID", "Idempotency-Key",
        "X-Tenant-ID", "X-Organization-ID", "X-Membership-ID",
        "X-Organization-Context",
    ],
)

from middleware.ratelimit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

from middleware.auth import SecurityMiddleware
app.add_middleware(SecurityMiddleware, api_key=settings.api_key)

from middleware.migration_gate import MigrationGateMiddleware
app.add_middleware(MigrationGateMiddleware)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}:\n{traceback.format_exc()}")
    from api_errors import error_body
    return JSONResponse(status_code=500, content=error_body(
        500, "Internal server error", getattr(request.state, "request_id", "")
    ))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from api_errors import error_body
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.status_code, exc.detail, getattr(request.state, "request_id", "")),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    from api_errors import error_body
    import json
    serializable_errors = json.loads(json.dumps(exc.errors(), default=str))
    return JSONResponse(status_code=422, content=error_body(
        422, serializable_errors, getattr(request.state, "request_id", "")
    ))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/readiness")
async def readiness(request: Request):
    ready = bool(getattr(request.app.state, "db_revision_ready", False))
    payload = {"status": "ready" if ready else "not_ready",
               "database_revision": getattr(request.app.state, "db_revision", None),
               "expected_revision": getattr(request.app.state, "db_revision_expected", None)}
    return JSONResponse(payload, status_code=200 if ready else 503)


from fastapi import Depends
from auth import AuthUser, require_roles


@app.get("/api/metrics")
async def metrics(user: AuthUser = Depends(require_roles("admin"))):
    from observability import snapshot
    return snapshot()


from api.documents import router as documents_router
app.include_router(documents_router)

from api.chat import router as chat_router
app.include_router(chat_router)

from api.conversations import router as conversations_router
app.include_router(conversations_router)

from api.settings import router as settings_router
app.include_router(settings_router)

from api.memories import router as memories_router
app.include_router(memories_router)

from api.auth import router as auth_router
app.include_router(auth_router)

from api.db_operations import router as db_operations_router
app.include_router(db_operations_router)

from api.usage import router as usage_router
app.include_router(usage_router)

from api.policies import router as policies_router
app.include_router(policies_router)

from api.telemetry import router as telemetry_router
app.include_router(telemetry_router)

from api.notifications import router as notifications_router
app.include_router(notifications_router)

from api.billing import router as billing_router
app.include_router(billing_router)

from api.tenants import router as tenants_router
app.include_router(tenants_router)

from api.departments import router as departments_router
app.include_router(departments_router)
