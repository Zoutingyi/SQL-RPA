import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from auth import AuthUser, get_current_user, require_roles
from organization_context import get_visible_organization_ids
from models.database import async_session
from models.schemas import Notification, NotificationEndpoint, NotificationPreference
from notifications import resolve_public_target

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

class EndpointInput(BaseModel):
    kind: str
    target: str
    enabled: bool = True

class PreferenceInput(BaseModel):
    event_type: str = "*"
    channel: str
    enabled: bool = True

def _validate_target(target: str) -> None:
    try:
        resolve_public_target(target)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

@router.get("")
async def list_notifications(unread_only: bool = False, user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        query = select(Notification).where(Notification.tenant_id.in_(scope_ids)).where((Notification.user_id.is_(None)) | (Notification.user_id == user.id))
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        items = (await session.execute(query.order_by(Notification.created_at.desc()).limit(100))).scalars().all()
        preferences = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.channel == "in_app",
        ))).scalars().all()
        disabled_exact = {row.event_type for row in preferences if row.event_type != "*" and not row.enabled}
        enabled_exact = {row.event_type for row in preferences if row.event_type != "*" and row.enabled}
        wildcard_disabled = any(row.event_type == "*" and not row.enabled for row in preferences)
        items = [item for item in items if item.event_type not in disabled_exact and
                 (not wildcard_disabled or item.event_type in enabled_exact)]
        return {"items": [{"id": n.id, "event_type": n.event_type, "title": n.title, "body": n.body,
                           "payload": n.payload, "read_at": n.read_at, "created_at": n.created_at} for n in items]}

@router.get("/unread-count")
async def unread_count(user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        event_types = (await session.execute(select(Notification.event_type).where(
            Notification.read_at.is_(None)).where(
            Notification.tenant_id.in_(scope_ids)).where(
            (Notification.user_id.is_(None)) | (Notification.user_id == user.id)))).scalars().all()
        preferences = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.channel == "in_app"))).scalars().all()
        disabled = {row.event_type for row in preferences if row.event_type != "*" and not row.enabled}
        enabled = {row.event_type for row in preferences if row.event_type != "*" and row.enabled}
        wildcard_disabled = any(row.event_type == "*" and not row.enabled for row in preferences)
        count = sum(event not in disabled and (not wildcard_disabled or event in enabled)
                    for event in event_types)
        return {"count": count}

@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        note = await session.scalar(select(Notification).where(
            Notification.id == notification_id, Notification.tenant_id.in_(scope_ids)))
        if not note or (note.user_id and note.user_id != user.id):
            raise HTTPException(404, "Notification not found")
        note.read_at = datetime.now(timezone.utc)
        await session.commit()
        return {"status": "read"}

@router.post("/endpoints", status_code=201)
async def create_endpoint(body: EndpointInput, _: AuthUser = Depends(require_roles("admin"))):
    if body.kind not in {"webhook", "email", "im"}:
        raise HTTPException(422, "kind must be webhook, email, or im")
    _validate_target(body.target)
    endpoint = NotificationEndpoint(id=str(uuid.uuid4()), tenant_id=_.tenant_id,
                                    kind=body.kind, target=body.target,
                                    enabled=body.enabled)
    async with async_session() as session:
        session.add(endpoint); await session.commit()
    return {"id": endpoint.id, "kind": endpoint.kind, "enabled": endpoint.enabled}

@router.get("/endpoints")
async def list_endpoints(_: AuthUser = Depends(require_roles("admin"))):
    scope_ids = await get_visible_organization_ids(_.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(select(NotificationEndpoint).where(
            NotificationEndpoint.tenant_id.in_(scope_ids)
        ).order_by(NotificationEndpoint.created_at.desc()))).scalars().all()
    return {"items": [{"id": row.id, "kind": row.kind, "target": row.target,
                       "enabled": row.enabled, "created_at": row.created_at} for row in rows]}

@router.put("/endpoints/{endpoint_id}")
async def update_endpoint(endpoint_id: str, body: EndpointInput,
                          _: AuthUser = Depends(require_roles("admin"))):
    if body.kind not in {"webhook", "email", "im"}:
        raise HTTPException(422, "kind must be webhook, email, or im")
    _validate_target(body.target)
    async with async_session() as session:
        row = await session.scalar(select(NotificationEndpoint).where(
            NotificationEndpoint.id == endpoint_id,
            NotificationEndpoint.tenant_id == _.tenant_id))
        if not row:
            raise HTTPException(404, "Notification endpoint not found")
        row.kind, row.target, row.enabled = body.kind, body.target, body.enabled
        await session.commit()
    return {"id": row.id, "kind": row.kind, "target": row.target, "enabled": row.enabled}

@router.delete("/endpoints/{endpoint_id}", status_code=204)
async def delete_endpoint(endpoint_id: str, _: AuthUser = Depends(require_roles("admin"))):
    async with async_session() as session:
        result = await session.execute(delete(NotificationEndpoint).where(
            NotificationEndpoint.id == endpoint_id,
            NotificationEndpoint.tenant_id == _.tenant_id))
        if result.rowcount != 1:
            raise HTTPException(404, "Notification endpoint not found")
        await session.commit()

@router.post("/endpoints/{endpoint_id}/test")
async def test_endpoint(endpoint_id: str, _: AuthUser = Depends(require_roles("admin"))):
    import httpx
    async with async_session() as session:
        row = await session.scalar(select(NotificationEndpoint).where(
            NotificationEndpoint.id == endpoint_id,
            NotificationEndpoint.tenant_id == _.tenant_id))
        if not row:
            raise HTTPException(404, "Notification endpoint not found")
    try:
        pinned, headers, extensions = resolve_public_target(row.target)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(pinned, headers=headers, extensions=extensions,
                json={"channel": row.kind,
                "event_type": "notification.test", "title": "SQL-RPA test notification",
                "body": "Notification endpoint is configured correctly.", "payload": {"test": True}})
            response.raise_for_status()
        return {"ok": True, "status_code": response.status_code}
    except Exception as exc:
        raise HTTPException(502, f"Test notification failed: {str(exc)[:300]}")

@router.get("/preferences")
async def get_preferences(user: AuthUser = Depends(get_current_user)):
    async with async_session() as session:
        rows = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.tenant_id == user.tenant_id))).scalars().all()
    return {"items": [{"event_type": row.event_type, "channel": row.channel,
                       "enabled": row.enabled} for row in rows]}

@router.put("/preferences")
async def set_preference(body: PreferenceInput, user: AuthUser = Depends(get_current_user)):
    if body.channel not in {"in_app", "webhook", "email", "im"}:
        raise HTTPException(422, "Unsupported notification channel")
    async with async_session() as session:
        row = await session.scalar(select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.tenant_id == user.tenant_id,
            NotificationPreference.event_type == body.event_type,
            NotificationPreference.channel == body.channel,
        ))
        if not row:
            row = NotificationPreference(id=str(uuid.uuid4()), tenant_id=user.tenant_id, user_id=user.id,
                event_type=body.event_type, channel=body.channel)
            session.add(row)
        row.enabled = body.enabled
        await session.commit()
    return {"event_type": row.event_type, "channel": row.channel, "enabled": row.enabled}
