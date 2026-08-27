"""Database revision gate used by startup and readiness."""
from dataclasses import dataclass

from sqlalchemy import text

from models.database import engine

EXPECTED_DATABASE_REVISION = "0008_user_phone_permission"


@dataclass(frozen=True)
class RevisionStatus:
    ready: bool
    current: str | None
    expected: str = EXPECTED_DATABASE_REVISION
    error: str | None = None


async def check_database_revision() -> RevisionStatus:
    try:
        async with engine.connect() as connection:
            current = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        return RevisionStatus(ready=current == EXPECTED_DATABASE_REVISION,
                              current=str(current) if current else None)
    except Exception as exc:
        return RevisionStatus(ready=False, current=None,
                              error=f"database revision check failed: {type(exc).__name__}")
