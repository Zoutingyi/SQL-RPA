"""API Key authentication + request body size limit via ASGI middleware."""
import secrets
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse

SKIP_PATHS = {"/api/health", "/api/auth/login"}
SKIP_SIZE_CHECK = {"/api/documents"}  # file upload has its own validation
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


class SecurityMiddleware:
    """ASGI middleware: Bearer token auth + request body size limit.

    Disabled when api_key is empty (development mode).
    Uses secrets.compare_digest for timing-safe token comparison.
    Uses native ASGI interface (not BaseHTTPMiddleware) to avoid
    conflicts with StreamingResponse.
    """

    def __init__(self, app: ASGIApp, api_key: str = "",
                 max_body_size: int = MAX_BODY_SIZE) -> None:
        self.app = app
        self._key = api_key
        self._max_body = max_body_size

    def _token_is_authorized(self, authorization: str) -> bool:
        """Return True when the Bearer token is the global API key or a valid JWT."""
        if not authorization.startswith("Bearer "):
            return False

        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            return False

        # Legacy single API key remains supported as a service identity.
        if self._key and secrets.compare_digest(token, self._key):
            return True

        # Production clients should authenticate as a real user with a JWT.
        try:
            from auth import decode_access_token

            decode_access_token(token)
            return True
        except Exception:
            return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # ── Body size check (skip file-upload paths) ──
        skip_size = any(path.startswith(p) for p in SKIP_SIZE_CHECK)
        if not skip_size:
            for k, v in scope.get("headers", []):
                if k == b"content-length" and int(v) > self._max_body:
                    from api_errors import error_body
                    resp = JSONResponse(
                        error_body(413, "Request body too large", code="PAYLOAD_TOO_LARGE"),
                        status_code=413
                    )
                    await resp(scope, receive, send)
                    return

        # ── Auth check ──
        if not self._key or path in SKIP_PATHS or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if not self._token_is_authorized(auth):
            from api_errors import error_body
            request_id = next((v.decode() for k, v in scope.get("headers", [])
                               if k.lower() == b"x-request-id"), "")
            response = JSONResponse(
                error_body(401, "Unauthorized", request_id), status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
