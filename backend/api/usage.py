"""LLM token usage aggregation endpoint."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from auth import AuthUser, require_roles
from config import settings
from models.database import async_session
from models.schemas import LlmDegradationEvent, LlmUsageLog, UsageQuota, UsageQuotaReservation
from organization_context import get_visible_organization_ids

router = APIRouter(prefix="/api/usage", tags=["usage"])


class QuotaBody(BaseModel):
    monthly_token_limit: int = Field(0, ge=0)
    monthly_cost_limit_usd: float = Field(0, ge=0)
    enabled: bool = True


@router.get("/summary")
async def usage_summary(
    days: int = 30,
    user: AuthUser = Depends(require_roles("approver")),
):
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        result = await session.execute(
            select(
                LlmUsageLog.model,
                func.count(LlmUsageLog.id),
                func.sum(LlmUsageLog.prompt_tokens),
                func.sum(LlmUsageLog.completion_tokens),
                func.sum(LlmUsageLog.total_tokens),
                func.sum(LlmUsageLog.cost_usd),
            )
            .where(LlmUsageLog.created_at >= since)
            .where(LlmUsageLog.tenant_id.in_(scope_ids))
            .group_by(LlmUsageLog.model)
            .order_by(func.sum(LlmUsageLog.total_tokens).desc())
        )
        rows = result.all()

    return {
        "days": days,
        "items": [
            {
                "model": model,
                "requests": requests or 0,
                "prompt_tokens": prompt_tokens or 0,
                "completion_tokens": completion_tokens or 0,
                "total_tokens": total_tokens or 0,
                "cost_usd": round(cost_usd or 0, 8),
            }
            for model, requests, prompt_tokens, completion_tokens, total_tokens, cost_usd in rows
        ],
    }


@router.get("/by-user")
async def usage_by_user(days: int = 30, user: AuthUser = Depends(require_roles("admin"))):
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(
                LlmUsageLog.user_id, func.count(LlmUsageLog.id),
                func.sum(LlmUsageLog.total_tokens), func.sum(LlmUsageLog.cost_usd),
            ).where(LlmUsageLog.created_at >= since,
                    LlmUsageLog.tenant_id.in_(scope_ids))
            .group_by(LlmUsageLog.user_id)
        )).all()
    return {"days": days, "items": [{
        "user_id": user_id or "unknown", "requests": requests or 0,
        "total_tokens": tokens or 0, "cost_usd": round(cost or 0, 8),
    } for user_id, requests, tokens, cost in rows]}


@router.get("/degradation-events")
async def degradation_events(user: AuthUser = Depends(require_roles("admin"))):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(LlmDegradationEvent).where(LlmDegradationEvent.tenant_id.in_(scope_ids))
            .order_by(LlmDegradationEvent.created_at.desc()).limit(100)
        )).scalars().all()
    return {"items": [{
        "id": row.id, "provider": row.provider, "primary_model": row.primary_model,
        "fallback_model": row.fallback_model, "event_type": row.event_type,
        "error_type": row.error_type, "created_at": row.created_at.isoformat(),
    } for row in rows]}


@router.put("/quotas/{user_id}")
async def set_quota(user_id: str, body: QuotaBody,
                    user: AuthUser = Depends(require_roles("admin"))):
    import uuid
    async with async_session() as session:
        row = await session.scalar(select(UsageQuota).where(
            UsageQuota.user_id == user_id, UsageQuota.tenant_id == user.tenant_id))
        if not row:
            row = UsageQuota(id=str(uuid.uuid4()), tenant_id=user.tenant_id, user_id=user_id)
            session.add(row)
        row.monthly_token_limit = body.monthly_token_limit
        row.monthly_cost_limit_usd = body.monthly_cost_limit_usd
        row.enabled = body.enabled
        await session.commit()
    return {"user_id": user_id, **body.model_dump()}


@router.get("/overview")
async def usage_overview(days: int = 30, user: AuthUser = Depends(require_roles("admin"))):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    summary = await usage_summary(days, user)
    users = await usage_by_user(days, user)
    async with async_session() as session:
        quotas = (await session.execute(select(UsageQuota).where(
            UsageQuota.tenant_id.in_(scope_ids)))).scalars().all()
    return {
        "days": days,
        "total_cost_usd": round(sum(item["cost_usd"] for item in summary["items"]), 8),
        "total_tokens": sum(item["total_tokens"] for item in summary["items"]),
        "by_model": summary["items"], "by_user": users["items"],
        "quotas": [{"user_id": q.user_id, "monthly_token_limit": q.monthly_token_limit,
                    "monthly_cost_limit_usd": q.monthly_cost_limit_usd,
                    "enabled": q.enabled} for q in quotas],
        "tenant_scope": "single_private_deployment",
    }


async def enforce_user_quota(user_id: str, tenant_id: str = "default") -> None:
    now = datetime.now(timezone.utc)
    since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as session:
        quota = await session.scalar(select(UsageQuota).where(
            UsageQuota.user_id == user_id, UsageQuota.tenant_id == tenant_id,
            UsageQuota.enabled.is_(True)
        ))
        if not quota:
            return
        tokens, cost = (await session.execute(select(
            func.coalesce(func.sum(LlmUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LlmUsageLog.cost_usd), 0),
        ).where(LlmUsageLog.user_id == user_id, LlmUsageLog.tenant_id == tenant_id,
                LlmUsageLog.created_at >= since))).one()
    if quota.monthly_token_limit and tokens >= quota.monthly_token_limit:
        raise HTTPException(status_code=429, detail="Monthly token quota exceeded")
    if quota.monthly_cost_limit_usd and cost >= quota.monthly_cost_limit_usd:
        raise HTTPException(status_code=429, detail="Monthly cost quota exceeded")


async def reserve_user_quota(user_id: str, request_id: str, tenant_id: str = "default") -> str | None:
    from organization_context import get_organization_context
    organization_context = get_organization_context()
    organization_id = (organization_context.organization_id
                       if organization_context else tenant_id)
    membership_id = organization_context.membership_id if organization_context else None
    scoped_request_id = f"{organization_id}|{request_id}"
    now = datetime.now(timezone.utc)
    since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    reservation_id = str(__import__("uuid").uuid4())
    async with async_session() as session:
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
        await session.execute(update(UsageQuotaReservation).where(
            UsageQuotaReservation.status == "active",
            UsageQuotaReservation.expires_at <= now,
        ).values(status="expired", settled_at=now))
        query = select(UsageQuota).where(
            UsageQuota.user_id == user_id, UsageQuota.tenant_id == tenant_id,
            UsageQuota.enabled.is_(True)
        )
        if session.get_bind().dialect.name != "sqlite":
            query = query.with_for_update()
        quota = await session.scalar(query)
        if not quota:
            await session.commit()
            return None
        tokens, cost = (await session.execute(select(
            func.coalesce(func.sum(LlmUsageLog.total_tokens), 0),
            func.coalesce(func.sum(LlmUsageLog.cost_usd), 0),
        ).where(LlmUsageLog.user_id == user_id, LlmUsageLog.tenant_id == tenant_id,
                LlmUsageLog.created_at >= since))).one()
        reserved_tokens, reserved_cost = (await session.execute(select(
            func.coalesce(func.sum(UsageQuotaReservation.reserved_tokens), 0),
            func.coalesce(func.sum(UsageQuotaReservation.reserved_cost_usd), 0),
        ).where(UsageQuotaReservation.user_id == user_id,
                UsageQuotaReservation.tenant_id == tenant_id,
                UsageQuotaReservation.status == "active"))).one()
        if quota.monthly_token_limit and (
            tokens + reserved_tokens + settings.quota_reservation_tokens > quota.monthly_token_limit
        ):
            await session.rollback()
            raise HTTPException(429, "Monthly token quota would be exceeded")
        if quota.monthly_cost_limit_usd and (
            cost + reserved_cost + settings.quota_reservation_cost_usd > quota.monthly_cost_limit_usd
        ):
            await session.rollback()
            raise HTTPException(429, "Monthly cost quota would be exceeded")
        session.add(UsageQuotaReservation(
            id=reservation_id, tenant_id=tenant_id, organization_id=organization_id,
            membership_id=membership_id, user_id=user_id, request_id=scoped_request_id,
            reserved_tokens=settings.quota_reservation_tokens,
            reserved_cost_usd=settings.quota_reservation_cost_usd,
            expires_at=now + timedelta(seconds=settings.quota_reservation_ttl_seconds),
        ))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(select(UsageQuotaReservation).where(
                UsageQuotaReservation.organization_id == organization_id,
                UsageQuotaReservation.request_id == scoped_request_id))
            if (existing and existing.user_id == user_id and
                    existing.membership_id == membership_id):
                return existing.id
            raise
    return reservation_id


async def settle_quota_reservation(reservation_id: str | None) -> None:
    if not reservation_id:
        return
    from organization_context import get_organization_context
    organization_context = get_organization_context()
    async with async_session() as session:
        conditions = [
            UsageQuotaReservation.id == reservation_id,
            UsageQuotaReservation.status == "active",
        ]
        if organization_context:
            conditions.extend([
                UsageQuotaReservation.organization_id == organization_context.organization_id,
                UsageQuotaReservation.membership_id == organization_context.membership_id,
            ])
        await session.execute(update(UsageQuotaReservation).where(
            *conditions).values(status="settled", settled_at=datetime.now(timezone.utc)))
        await session.commit()
