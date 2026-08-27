"""Validated request-scoped organization identity and scope resolution."""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.schemas import OrganizationMembership, OrganizationUnit


@dataclass(frozen=True)
class OrganizationContext:
    user_id: str
    company_id: str
    organization_id: str
    membership_id: str
    organization_level: str
    role: str
    path: str
    context_version: int
    is_primary: bool


_organization_context: ContextVar[OrganizationContext | None] = ContextVar(
    "sql_rpa_organization_context", default=None
)


def get_organization_context() -> OrganizationContext | None:
    return _organization_context.get()


def set_organization_context(context: OrganizationContext):
    return _organization_context.set(context)


def reset_organization_context(token) -> None:
    _organization_context.reset(token)


def _effective(value: datetime | None, now: datetime, *, start: bool) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= now if start else value > now


async def resolve_organization_context(session: AsyncSession, *, user_id: str,
                                       organization_id: str,
                                       membership_id: str) -> OrganizationContext:
    row = (await session.execute(
        select(OrganizationMembership, OrganizationUnit)
        .join(OrganizationUnit, OrganizationUnit.id == OrganizationMembership.organization_id)
        .where(OrganizationMembership.id == membership_id,
               OrganizationMembership.user_id == user_id,
               OrganizationMembership.organization_id == organization_id,
               OrganizationMembership.active.is_(True),
               OrganizationUnit.active.is_(True))
    )).one_or_none()
    if not row:
        raise HTTPException(status_code=403, detail="Invalid organization context")
    membership, unit = row
    now = datetime.now(timezone.utc)
    if (not _effective(membership.valid_from, now, start=True) or
            not _effective(membership.valid_to, now, start=False)):
        raise HTTPException(status_code=403, detail="Organization membership is not effective")
    level = unit.level.value if hasattr(unit.level, "value") else str(unit.level)
    membership_level = (membership.organization_level.value
                        if hasattr(membership.organization_level, "value")
                        else str(membership.organization_level))
    if membership_level != level:
        raise HTTPException(status_code=403, detail="Organization membership level mismatch")
    path_ids = unit.path.split("/")
    ancestors = (await session.execute(select(OrganizationUnit.id).where(
        OrganizationUnit.id.in_(path_ids), OrganizationUnit.active.is_(True),
        OrganizationUnit.company_id == unit.company_id))).scalars().all()
    if len(set(ancestors)) != len(path_ids) or path_ids[-1] != unit.id:
        raise HTTPException(status_code=403, detail="Organization path is invalid")
    return OrganizationContext(
        user_id=user_id, company_id=unit.company_id, organization_id=unit.id,
        membership_id=membership.id, organization_level=level, role=membership.role,
        path=unit.path, context_version=unit.context_version,
        is_primary=membership.is_primary,
    )


async def resolve_scope_node_ids(session: AsyncSession,
                                 context: OrganizationContext) -> set[str]:
    if context.organization_level == "individual":
        return {context.organization_id}
    rows = (await session.execute(select(OrganizationUnit.id).where(
        OrganizationUnit.company_id == context.company_id,
        OrganizationUnit.active.is_(True),
        (OrganizationUnit.path == context.path) |
        OrganizationUnit.path.startswith(f"{context.path}/"),
    ))).scalars().all()
    return set(rows)


def require_resource_scope(context: OrganizationContext, *, company_id: str,
                           organization_id: str, allowed_ids: set[str],
                           owner_id: str | None = None) -> None:
    if company_id != context.company_id or organization_id not in allowed_ids:
        raise HTTPException(status_code=403, detail="Resource is outside the organization scope")
    if context.organization_level == "individual" and owner_id != context.user_id:
        raise HTTPException(status_code=403, detail="Resource is outside the individual scope")


async def get_visible_organization_ids(legacy_tenant_id: str) -> set[str]:
    """Resolve the current immutable context to IDs usable by legacy tenant columns."""
    context = get_organization_context()
    if not context:
        return {legacy_tenant_id}
    from models.database import async_session
    async with async_session() as session:
        return await resolve_scope_node_ids(session, context)


async def get_resource_scope(legacy_tenant_id: str, *, user_id: str | None = None,
                             legacy_owner_required: bool = False
                             ) -> tuple[set[str], bool]:
    """Return visible node IDs and whether owner filtering is mandatory.

    The owner flag makes the individual-level rule impossible for callers to
    forget while legacy/non-organization requests retain their prior behavior.
    """
    context = get_organization_context()
    ids = await get_visible_organization_ids(legacy_tenant_id)
    return ids, bool(user_id and (
        (context is None and legacy_owner_required) or
        (context is not None and context.organization_level == "individual")
    ))


def current_write_scope(legacy_tenant_id: str) -> tuple[str, str | None, str | None]:
    """Canonical organization/company/membership attribution for new writes."""
    context = get_organization_context()
    if not context:
        return legacy_tenant_id, None, None
    return context.organization_id, context.company_id, context.membership_id
