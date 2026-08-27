"""In-memory sliding-window rate limiter per client IP."""
import os
import hashlib
import time
import uuid
from collections import defaultdict
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse

RATE_LIMIT = int(os.environ.get("SQL_RPA_RATE_LIMIT", "30"))
RATE_WINDOW = int(os.environ.get("SQL_RPA_RATE_WINDOW", "60"))
SKIP_PATHS = {"/api/health"}
SENSITIVE_LIMITS = {
    "/api/auth/users": (
        int(os.environ.get("SQL_RPA_USER_CREATE_RATE_LIMIT", "10")), 60),
    "/api/auth/change-password": (
        int(os.environ.get("SQL_RPA_PASSWORD_CHANGE_RATE_LIMIT", "5")), 300),
}


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._sensitive_windows: dict[str, list[float]] = defaultdict(list)
        self._redis = None

    async def _redis_limited(self, bucket: str, now: float, limit: int,
                             window_seconds: int) -> bool | None:
        from config import settings
        if not settings.redis_url:
            return None
        try:
            if self._redis is None:
                from redis.asyncio import from_url
                self._redis = from_url(settings.redis_url, decode_responses=True)
            key = f"sql_rpa:rate:{bucket}"
            member = f"{now}:{uuid.uuid4()}"
            pipe = self._redis.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.expire(key, window_seconds + 1)
            _, _, count, _ = await pipe.execute()
            return count > limit
        except Exception:
            # Availability wins over rate limiting if Redis is temporarily down;
            # metrics/logging should alert operators to the dependency failure.
            return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in SKIP_PATHS or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        client_ip = scope.get("client", ("unknown", 0))[0]
        now = time.monotonic()
        sensitive = SENSITIVE_LIMITS.get(path) if scope.get("method") == "POST" else None
        if sensitive:
            limit, sensitive_window = sensitive
            authorization = dict(scope.get("headers", [])).get(b"authorization", b"")
            identity = hashlib.sha256(authorization).hexdigest()[:20]
            bucket = f"sensitive:{path}:{client_ip}:{identity}"
            redis_sensitive = await self._redis_limited(
                bucket, time.time(), limit, sensitive_window)
            limited = redis_sensitive
            if limited is None:
                window = self._sensitive_windows[bucket]
                cutoff = now - sensitive_window
                while window and window[0] < cutoff:
                    window.pop(0)
                limited = len(window) >= limit
                if not limited:
                    window.append(now)
            if limited:
                from api_errors import error_body
                request_id = next((v.decode() for k, v in scope.get("headers", [])
                                   if k.lower() == b"x-request-id"), "")
                response = JSONResponse(error_body(
                    429, "Sensitive operation rate limit exceeded", request_id,
                    code="SENSITIVE_RATE_LIMITED"), status_code=429,
                    headers={"Retry-After": str(sensitive_window)})
                await response(scope, receive, send)
                return

        redis_limited = await self._redis_limited(
            f"global:{client_ip}", time.time(), RATE_LIMIT, RATE_WINDOW)
        if redis_limited is not None:
            if redis_limited:
                from api_errors import error_body
                response = JSONResponse(
                    error_body(429, "Too many requests. Please slow down."),
                    status_code=429, headers={"Retry-After": str(RATE_WINDOW)},
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        window = self._windows[client_ip]

        cutoff = now - RATE_WINDOW
        while window and window[0] < cutoff:
            window.pop(0)

        if len(window) >= RATE_LIMIT:
            from api_errors import error_body
            request_id = next((v.decode() for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"), "")
            response = JSONResponse(
                error_body(429, "Too many requests. Please slow down.", request_id),
                status_code=429,
                headers={"Retry-After": str(RATE_WINDOW)},
            )
            await response(scope, receive, send)
            return

        window.append(now)
        await self.app(scope, receive, send)
