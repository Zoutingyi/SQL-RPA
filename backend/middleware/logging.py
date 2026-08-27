"""Structured logging with request_id tracking and log rotation."""
import json
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_access_logger: logging.Logger | None = None


def _get_access_logger() -> logging.Logger:
    global _access_logger
    if _access_logger is not None:
        return _access_logger

    from config import settings
    from pathlib import Path

    log_dir = Path(settings.upload_dir).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    handler = RotatingFileHandler(
        log_dir / "access.log",
        maxBytes=10 * 1024 * 1024,   # 10 MB
        backupCount=5,                 # keep 5 historical files
        encoding="utf-8",
        delay=True,                    # defer file creation until first write
    )

    _access_logger = logging.getLogger("sql_rpa.access")
    _access_logger.setLevel(logging.INFO)
    _access_logger.propagate = False
    _access_logger.addHandler(handler)
    return _access_logger


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        request.state.request_id = request_id
        t0 = time.time()

        response = await call_next(request)

        elapsed_ms = int((time.time() - t0) * 1000)
        from observability import observe
        observe("http", str(response.status_code), elapsed_ms)
        if elapsed_ms >= 1000:
            logging.getLogger("sql_rpa").warning(
                "Slow request rid=%s %s %s elapsed_ms=%s",
                request_id, request.method, request.url.path, elapsed_ms,
            )
        _log_request(request_id, request.method, request.url.path,
                     response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = request_id
        return response


def _log_request(request_id: str, method: str, path: str,
                 status: int, elapsed_ms: int) -> None:
    try:
        logger = _get_access_logger()
        record = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "rid": request_id,
            "method": method,
            "path": path,
            "status": status,
            "elapsed_ms": elapsed_ms,
        }, ensure_ascii=False)
        logger.info(record)
    except Exception:
        pass  # logging failure must not break the application
