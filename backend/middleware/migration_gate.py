"""Block business traffic while the internal database is behind migrations."""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class MigrationGateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        testing = bool(app and getattr(app.state, "testing", False))
        ready = bool(app and getattr(app.state, "db_revision_ready", False))
        if app and not testing and not ready:
            from migration_gate import check_database_revision
            revision = await check_database_revision()
            app.state.db_revision_ready = revision.ready
            app.state.db_revision = revision.current
            app.state.db_revision_expected = revision.expected
            ready = revision.ready
        if (not testing and not ready
                and scope.get("path") not in {"/api/health", "/api/readiness"}):
            from api_errors import error_body
            response = JSONResponse(error_body(
                503, "Database migration is required", code="DATABASE_REVISION_MISMATCH"),
                status_code=503)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
