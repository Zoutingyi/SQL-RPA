"""Four-level department tree, memberships, and context switching APIs."""
from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from auth import (AuthUser, create_organization_context_token, get_current_user,
                  require_roles)
from models.database import async_session
from models.schemas import (DomainEvent, OrganizationLevel, OrganizationMembership,
                            OrganizationUnit, User)
from organization_context import (get_organization_context,
                                  resolve_organization_context,
                                  resolve_scope_node_ids)
from organization_service import (OrganizationError, create_organization_membership,
                                  create_organization_unit, disable_membership,
                                  move_organization_unit, set_primary_membership)

router = APIRouter(prefix="/api/departments", tags=["departments"])


class UnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    level: OrganizationLevel
    parent_id: str | None = None
    sort_order: int = 0


class UnitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    sort_order: int = 0
    version: int = Field(ge=1)


class UnitMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: str
    version: int = Field(ge=1)


class MembershipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    role: str | None = None
    job_title: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class MembershipUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = None
    job_title: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    version: int = Field(ge=1)


class PrimaryRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class DisableMembershipRequest(BaseModel):
    replacement_primary_id: str | None = None
    reason: str = Field(min_length=1, max_length=500)


class ContextSwitchRequest(BaseModel):
    membership_id: str


def _context_or_400():
    context = get_organization_context()
    if not context:
        raise HTTPException(400, "Organization context is required")
    return context


def _unit_dict(unit: OrganizationUnit, member_count: int = 0) -> dict:
    return {
        "id": unit.id, "name": unit.name,
        "level": unit.level.value if hasattr(unit.level, "value") else str(unit.level),
        "parent_id": unit.parent_id, "company_id": unit.company_id,
        "path": unit.path, "depth": unit.depth, "active": unit.active,
        "sort_order": unit.sort_order, "member_count": member_count,
        "version": unit.context_version,
    }


def _membership_dict(membership: OrganizationMembership, unit: OrganizationUnit) -> dict:
    return {
        "id": membership.id, "user_id": membership.user_id,
        "organization_id": unit.id, "organization_name": unit.name,
        "organization_level": unit.level.value if hasattr(unit.level, "value") else str(unit.level),
        "company_id": unit.company_id, "path": unit.path, "role": membership.role,
        "job_title": membership.job_title, "is_primary": membership.is_primary,
        "active": membership.active, "valid_from": membership.valid_from,
        "valid_to": membership.valid_to, "version": membership.version,
    }


async def _audit(user: AuthUser, request: Request, event_type: str,
                 aggregate_id: str, payload: dict):
    context = get_organization_context()
    tenant_id = context.organization_id if context else user.tenant_id
    async with async_session() as session:
        session.add(DomainEvent(id=str(uuid.uuid4()), tenant_id=tenant_id,
            aggregate_type="organization", aggregate_id=aggregate_id,
            event_type=event_type, payload={**payload, "actor_id": user.id,
                "company_id": context.company_id if context else None,
                "organization_id": context.organization_id if context else None,
                "membership_id": context.membership_id if context else None,
                "request_id": getattr(request.state, "request_id", ""),
                "source_ip": request.client.host if request.client else None,
                "result": "success"}))
        await session.commit()


def _organization_error(exc: OrganizationError):
    status = 409 if exc.code in {"ORG_VERSION_CONFLICT", "ORG_MEMBERSHIP_EXISTS"} else 422
    raise HTTPException(status, {"code": exc.code, "message": str(exc)}) from exc


@router.get("/tree")
async def organization_tree(user: AuthUser = Depends(get_current_user)):
    context = _context_or_400()
    async with async_session() as session:
        visible_ids = await resolve_scope_node_ids(session, context)
        rows = (await session.execute(
            select(OrganizationUnit,
                   func.count(OrganizationMembership.id).label("member_count"))
            .outerjoin(OrganizationMembership,
                       (OrganizationMembership.organization_id == OrganizationUnit.id) &
                       OrganizationMembership.active.is_(True))
            .where(OrganizationUnit.id.in_(visible_ids), OrganizationUnit.active.is_(True))
            .group_by(OrganizationUnit.id)
            .order_by(OrganizationUnit.path, OrganizationUnit.sort_order)
        )).all()
    return {"current": context.organization_id,
            "items": [_unit_dict(unit, count) for unit, count in rows]}


@router.get("/memberships/me")
async def my_memberships(user: AuthUser = Depends(get_current_user)):
    async with async_session() as session:
        rows = (await session.execute(select(OrganizationMembership, OrganizationUnit).join(
            OrganizationUnit, OrganizationUnit.id == OrganizationMembership.organization_id
        ).where(OrganizationMembership.user_id == user.id,
                OrganizationMembership.active.is_(True),
                OrganizationUnit.active.is_(True)).order_by(
            OrganizationMembership.is_primary.desc(), OrganizationUnit.depth,
            OrganizationMembership.created_at))).all()
    return {"items": [_membership_dict(membership, unit) for membership, unit in rows]}


@router.get("/{unit_id}")
async def get_unit(unit_id: str, user: AuthUser = Depends(get_current_user)):
    context = _context_or_400()
    async with async_session() as session:
        visible = await resolve_scope_node_ids(session, context)
        if unit_id not in visible:
            raise HTTPException(403, "Organization is outside the current scope")
        unit = await session.get(OrganizationUnit, unit_id)
    return _unit_dict(unit)


@router.post("", status_code=201)
async def create_unit(body: UnitCreate, request: Request,
                      user: AuthUser = Depends(require_roles("admin"))):
    context = get_organization_context()
    if body.level == OrganizationLevel.company:
        if context is not None:
            raise HTTPException(403, "Company creation requires the platform administration context")
    else:
        context = _context_or_400()
        async with async_session() as session:
            visible = await resolve_scope_node_ids(session, context)
        if body.parent_id not in visible:
            raise HTTPException(403, "Parent organization is outside the current scope")
    try:
        async with async_session() as session:
            unit = await create_organization_unit(session, name=body.name, level=body.level,
                parent_id=body.parent_id, sort_order=body.sort_order)
            await session.commit()
            result = _unit_dict(unit)
    except OrganizationError as exc:
        _organization_error(exc)
    except IntegrityError as exc:
        raise HTTPException(409, "Organization conflicts with an existing node") from exc
    await _audit(user, request, "organization.created", result["id"], result)
    return result


@router.put("/{unit_id}")
async def update_unit(unit_id: str, body: UnitUpdate, request: Request,
                      user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        visible = await resolve_scope_node_ids(session, context)
        if unit_id not in visible:
            raise HTTPException(403, "Organization is outside the current scope")
        unit = await session.scalar(select(OrganizationUnit).where(
            OrganizationUnit.id == unit_id).with_for_update())
        if not unit or unit.context_version != body.version:
            raise HTTPException(409, "Organization version changed")
        if not body.name.strip():
            raise HTTPException(422, "Organization name is required")
        unit.name, unit.sort_order = body.name.strip(), body.sort_order
        unit.context_version += 1
        try:
            await session.commit()
        except IntegrityError as exc:
            raise HTTPException(409, "Organization name already exists") from exc
        result = _unit_dict(unit)
    await _audit(user, request, "organization.updated", unit_id, result)
    return result


@router.post("/{unit_id}/move")
async def move_unit(unit_id: str, body: UnitMove, request: Request,
                    user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        visible = await resolve_scope_node_ids(session, context)
        if unit_id not in visible or body.parent_id not in visible:
            raise HTTPException(403, "Organization is outside the current scope")
        try:
            unit = await move_organization_unit(session, unit_id=unit_id,
                                                new_parent_id=body.parent_id,
                                                expected_version=body.version)
            await session.commit()
            result = _unit_dict(unit)
        except OrganizationError as exc:
            _organization_error(exc)
    await _audit(user, request, "organization.moved", unit_id,
                 {"new_parent_id": body.parent_id, "version": result["version"]})
    return result


@router.post("/{unit_id}/disable")
async def disable_unit(unit_id: str, request: Request,
                       user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        visible = await resolve_scope_node_ids(session, context)
        if unit_id not in visible or unit_id == context.organization_id:
            raise HTTPException(403, "Organization cannot be disabled from this context")
        children = await session.scalar(select(func.count()).select_from(OrganizationUnit).where(
            OrganizationUnit.parent_id == unit_id, OrganizationUnit.active.is_(True)))
        members = await session.scalar(select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.organization_id == unit_id,
            OrganizationMembership.active.is_(True)))
        if children or members:
            raise HTTPException(409, "Organization has active children or memberships")
        unit = await session.get(OrganizationUnit, unit_id)
        if not unit:
            raise HTTPException(403, "Organization is outside the current scope")
        unit.active = False
        unit.context_version += 1
        await session.commit()
    await _audit(user, request, "organization.disabled", unit_id, {})
    return {"id": unit_id, "active": False}


@router.post("/{unit_id}/memberships", status_code=201)
async def add_membership(unit_id: str, body: MembershipCreate, request: Request,
                         user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        visible = await resolve_scope_node_ids(session, context)
        if unit_id not in visible:
            raise HTTPException(403, "Organization is outside the current scope")
        try:
            membership = await create_organization_membership(session,
                user_id=body.user_id, organization_id=unit_id,
                role=body.role or "unassigned", job_title=body.job_title,
                valid_from=body.valid_from, valid_to=body.valid_to, created_by=user.id)
            unit = await session.get(OrganizationUnit, unit_id)
            await session.commit()
            result = _membership_dict(membership, unit)
        except OrganizationError as exc:
            _organization_error(exc)
    await _audit(user, request, "organization.membership.created", membership.id, result)
    return result


@router.get("/{unit_id}/memberships")
async def list_memberships(unit_id: str,
                           user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        visible = await resolve_scope_node_ids(session, context)
        if unit_id not in visible:
            raise HTTPException(403, "Organization is outside the current scope")
        rows = (await session.execute(
            select(OrganizationMembership, OrganizationUnit)
            .join(OrganizationUnit,
                  OrganizationUnit.id == OrganizationMembership.organization_id)
            .where(OrganizationMembership.organization_id == unit_id,
                   OrganizationMembership.active.is_(True))
            .order_by(OrganizationMembership.is_primary.desc(),
                      OrganizationMembership.created_at)
        )).all()
    return {"items": [_membership_dict(membership, unit)
                      for membership, unit in rows]}


@router.put("/memberships/{membership_id}")
async def update_membership(membership_id: str, body: MembershipUpdate, request: Request,
                            user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        membership = await session.scalar(select(OrganizationMembership).where(
            OrganizationMembership.id == membership_id).with_for_update())
        if not membership:
            raise HTTPException(403, "Membership is outside the current scope")
        visible = await resolve_scope_node_ids(session, context)
        if membership.organization_id not in visible:
            raise HTTPException(403, "Membership is outside the current scope")
        if membership.version != body.version:
            raise HTTPException(409, "Membership version changed")
        if body.valid_from and body.valid_to and body.valid_to <= body.valid_from:
            raise HTTPException(422, "Invalid membership validity period")
        membership.role = body.role if body.role is not None else membership.role
        membership.job_title = body.job_title
        membership.valid_from, membership.valid_to = body.valid_from, body.valid_to
        membership.version += 1
        unit = await session.get(OrganizationUnit, membership.organization_id)
        await session.commit()
        result = _membership_dict(membership, unit)
    await _audit(user, request, "organization.membership.updated", membership_id, result)
    return result


@router.post("/memberships/{membership_id}/set-primary")
async def set_primary(membership_id: str, body: PrimaryRequest, request: Request,
                      user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        target = await session.get(OrganizationMembership, membership_id)
        visible = await resolve_scope_node_ids(session, context)
        if not target or target.organization_id not in visible:
            raise HTTPException(403, "Membership is outside the current scope")
        try:
            old_id, new_id = await set_primary_membership(session, membership_id)
            await session.commit()
        except OrganizationError as exc:
            _organization_error(exc)
    await _audit(user, request, "organization.membership.primary_changed", membership_id,
                 {"old_membership_id": old_id, "new_membership_id": new_id,
                  "reason": body.reason})
    return {"old_membership_id": old_id, "new_membership_id": new_id}


@router.post("/memberships/{membership_id}/disable")
async def disable_member(membership_id: str, body: DisableMembershipRequest, request: Request,
                         user: AuthUser = Depends(require_roles("admin"))):
    context = _context_or_400()
    async with async_session() as session:
        target = await session.get(OrganizationMembership, membership_id)
        visible = await resolve_scope_node_ids(session, context)
        if not target or target.organization_id not in visible:
            raise HTTPException(403, "Membership is outside the current scope")
        try:
            membership = await disable_membership(
                session, membership_id, body.replacement_primary_id)
            await session.commit()
        except OrganizationError as exc:
            _organization_error(exc)
    await _audit(user, request, "organization.membership.disabled", membership_id,
                 {"reason": body.reason})
    return {"id": membership.id, "active": False}


@router.post("/context/switch")
async def switch_context(body: ContextSwitchRequest, request: Request,
                         user: AuthUser = Depends(get_current_user)):
    async with async_session() as session:
        membership = await session.get(OrganizationMembership, body.membership_id)
        if not membership:
            raise HTTPException(403, "Invalid organization context")
        context = await resolve_organization_context(session, user_id=user.id,
            organization_id=membership.organization_id, membership_id=membership.id)
        db_user = await session.get(User, user.id)
    token = create_organization_context_token(db_user, context)
    await _audit(user, request, "organization.context.switched", context.organization_id,
                 {"target_membership_id": context.membership_id,
                  "target_organization_id": context.organization_id})
    return {"context_token": token, "token_type": "bearer", "expires_in": 900,
            "company_id": context.company_id, "organization_id": context.organization_id,
            "membership_id": context.membership_id,
            "organization_level": context.organization_level,
            "organization_path": context.path, "role": context.role,
            "context_version": context.context_version,
            "is_primary": context.is_primary}
