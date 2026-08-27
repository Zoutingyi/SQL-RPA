import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from auth import AuthUser, get_current_user, require_roles
from organization_context import get_visible_organization_ids
from models.database import async_session
from models.schemas import FrontendTelemetry
from observability import observe

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


class TelemetryBody(BaseModel):
    event_type: str = Field(pattern="^(error|performance|navigation)$")
    message: str = Field("", max_length=4000)
    page: str = Field("", max_length=500)
    request_id: str = Field("", max_length=100)
    duration_ms: int | None = Field(None, ge=0)
    payload: dict = {}


@router.post("")
async def report_telemetry(body: TelemetryBody, user: AuthUser = Depends(get_current_user)):
    async with async_session() as session:
        row = FrontendTelemetry(
            id=str(uuid.uuid4()), tenant_id=user.tenant_id, user_id=user.id, **body.model_dump()
        )
        session.add(row)
        await session.commit()
    observe("frontend", body.event_type, body.duration_ms or 0)
    return {"accepted": True, "id": row.id}


@router.get("")
async def list_telemetry(user: AuthUser = Depends(require_roles("admin"))):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(FrontendTelemetry).where(FrontendTelemetry.tenant_id.in_(scope_ids))
            .order_by(FrontendTelemetry.created_at.desc()).limit(200)
        )).scalars().all()
    return {"items": [{
        "id": row.id, "user_id": row.user_id, "event_type": row.event_type,
        "message": row.message, "page": row.page, "request_id": row.request_id,
        "duration_ms": row.duration_ms, "payload": row.payload,
        "created_at": row.created_at.isoformat(),
    } for row in rows]}
