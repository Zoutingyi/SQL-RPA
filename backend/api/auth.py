"""Authentication endpoints for login and current-user lookup."""

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, func
from sqlalchemy.exc import IntegrityError

from auth import (
    AuthUser,
    create_access_token,
    get_current_user,
    hash_password,
    login_lock_remaining,
    normalize_username,
    record_login_failure,
    require_roles,
    reset_login_failures,
    verify_password,
)
from models.database import async_session
from config import settings
from models.schemas import (DomainEvent, Membership, OrganizationMembership, OrganizationUnit,
                            Tenant, User, UserCreateIdempotency, UserRole)
from organization_context import get_organization_context, resolve_scope_node_ids
from user_pii import decrypt_phone, encrypt_phone, masked_phone, phone_lookup_hash

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def require_platform_user_admin(
        user: AuthUser = Depends(require_roles("admin"))) -> AuthUser:
    """Global User mutations are never available to tenant-only admins."""
    if settings.multi_tenant_enabled and not user.is_platform_admin and user.auth_type != "dev":
        raise HTTPException(status_code=403, detail="Platform user management permission required")
    return user


async def require_full_phone_access(
        user: AuthUser = Depends(require_platform_user_admin)) -> AuthUser:
    async with async_session() as session:
        identity = await session.get(User, user.id)
    if not identity or not identity.can_view_full_phone:
        raise HTTPException(status_code=403, detail="Full phone access permission required")
    return user


async def _lock_platform_admin_mutation(session) -> None:
    """Serialize the invariant that at least one active platform admin remains."""
    bind = session.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        # Stable application-scoped advisory lock, held until transaction end.
        await session.execute(select(func.pg_advisory_xact_lock(0x53514C525041)))
    elif dialect == "sqlite":
        from sqlalchemy import text
        await session.execute(text("BEGIN IMMEDIATE"))
    else:
        # Row locks provide the best available serialization on other engines.
        await session.execute(select(User.id).where(
            User.is_platform_admin.is_(True)).with_for_update())


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    organization_id: str = Field(min_length=1, max_length=36)
    job_title: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=32)
    password: str | None = None
    role: UserRole | None = None


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=1, max_length=32)
    is_active: bool | None = None
    can_view_full_phone: bool | None = None
    version: int = Field(ge=1)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str
    confirm_password: str


class MembershipSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    organization_id: str
    organization_name: str
    job_title: str | None = None
    role: str | None = None
    is_primary: bool


class UserSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    username: str
    display_name: str
    phone: str | None = None
    is_platform_admin: bool
    must_change_password: bool


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    access_token: str
    token_type: str
    organization: dict | None
    current_membership: MembershipSummary | None
    organization_memberships: list[MembershipSummary]
    user: UserSummary


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    username: str
    display_name: str
    phone: str | None
    organization: dict | None
    current_membership: MembershipSummary | None
    organization_memberships: list[MembershipSummary]
    is_platform_admin: bool
    must_change_password: bool


class UserListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[dict]
    page: int
    page_size: int
    total: int
    pages: int


class CreateUserResponse(BaseModel):
    user: UserSummary
    primary_membership: MembershipSummary
    used_default_password: bool
    must_change_password: bool


def _membership_to_dict(membership: OrganizationMembership,
                        unit: OrganizationUnit) -> dict:
    role = membership.role
    return {
        "id": membership.id, "membership_id": membership.id, "organization_id": unit.id,
        "organization_name": unit.name, "organization_level": (
            unit.level.value if hasattr(unit.level, "value") else str(unit.level)),
        "company_id": unit.company_id, "organization_path": unit.path,
        "job_title": membership.job_title,
        "role": None if role == "unassigned" else role,
        "is_primary": membership.is_primary, "active": membership.active,
        "valid_from": membership.valid_from, "valid_to": membership.valid_to,
        "version": membership.version,
    }


def _effective_membership_clause(now: datetime):
    return (
        OrganizationMembership.active.is_(True),
        or_(OrganizationMembership.valid_from.is_(None),
            OrganizationMembership.valid_from <= now),
        or_(OrganizationMembership.valid_to.is_(None),
            OrganizationMembership.valid_to > now),
        OrganizationUnit.active.is_(True),
    )


def _add_user_audit(session, request: Request, actor: AuthUser, event_type: str,
                    target_id: str, payload: dict | None = None) -> None:
    session.add(DomainEvent(
        id=str(uuid.uuid4()), tenant_id=actor.organization_id or actor.tenant_id or "platform",
        aggregate_type="user", aggregate_id=target_id, event_type=event_type,
        payload={**(payload or {}), "actor_id": actor.id,
                 "company_id": actor.company_id,
                 "organization_id": actor.organization_id,
                 "membership_id": actor.membership_id,
                 "source_ip": request.client.host if request.client else None,
                 "request_id": getattr(request.state, "request_id", "")},
    ))


async def _write_user_failure_audit(request: Request, actor: AuthUser,
                                    event_type: str, target_id: str,
                                    failure_code: str) -> None:
    async with async_session() as session:
        _add_user_audit(session, request, actor, event_type, target_id,
                        {"result": "failure", "failure_code": failure_code})
        await session.commit()


async def _write_login_audit(request: Request, target: User | None,
                             event_type: str, result: str,
                             *, first_default_admin_login: bool = False) -> None:
    target_id = target.id if target else hashlib.sha256(
        f"{request.client.host if request.client else 'unknown'}:unknown".encode()
    ).hexdigest()[:36]
    async with async_session() as session:
        session.add(DomainEvent(
            id=str(uuid.uuid4()), tenant_id="platform", aggregate_type="authentication",
            aggregate_id=target_id, event_type=event_type,
            payload={"target_user_id": target.id if target else None,
                     "source_ip": request.client.host if request.client else None,
                     "request_id": getattr(request.state, "request_id", ""),
                     "result": result,
                     "first_default_admin_login": first_default_admin_login},
        ))
        await session.commit()


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    login_name = normalize_username(body.username)
    lock_remaining = await login_lock_remaining(login_name)
    if lock_remaining > 0:
        await _write_login_audit(request, None, "login.locked", "locked")
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {int(lock_remaining)} seconds.",
        )

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.username_normalized == normalize_username(body.username))
        )
        user = result.scalar_one_or_none()

        if not user:
            await record_login_failure(login_name)
            await _write_login_audit(request, None, "login.failed", "invalid_credentials")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        if not user.is_active:
            await record_login_failure(login_name)
            await _write_login_audit(request, user, "login.failed", "inactive_account")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        if not verify_password(body.password, user.password_hash):
            await record_login_failure(login_name)
            await _write_login_audit(request, user, "login.failed", "invalid_credentials")
            raise HTTPException(status_code=401, detail="Invalid username or password")

        memberships = (await session.execute(
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(Membership.user_id == user.id,
                   Membership.active.is_(True), Tenant.active.is_(True))
            .order_by(Membership.created_at.asc())
        )).all()
        organization_memberships = (await session.execute(
            select(OrganizationMembership, OrganizationUnit)
            .join(OrganizationUnit,
                  OrganizationUnit.id == OrganizationMembership.organization_id)
            .where(OrganizationMembership.user_id == user.id,
                   OrganizationMembership.active.is_(True),
                   OrganizationUnit.active.is_(True))
            .order_by(OrganizationMembership.is_primary.desc(),
                      OrganizationUnit.depth.desc(), OrganizationMembership.created_at.asc())
        )).all()

    now = datetime.now(timezone.utc)
    organization_memberships = [item for item in organization_memberships if
        (item[0].valid_from is None or item[0].valid_from.replace(
            tzinfo=item[0].valid_from.tzinfo or timezone.utc) <= now) and
        (item[0].valid_to is None or item[0].valid_to.replace(
            tzinfo=item[0].valid_to.tzinfo or timezone.utc) > now)]

    if (settings.multi_tenant_enabled and not user.is_platform_admin
            and not memberships and not organization_memberships):
        raise HTTPException(status_code=403, detail="No active organization membership")

    # Prefer the configured default when accessible; otherwise choose the first
    # active membership so a SaaS user never needs to know a tenant UUID to log in.
    selected = next((item for item in memberships
                     if item[0].tenant_id == settings.default_tenant_id), None)
    selected = selected or (memberships[0] if memberships else None)
    tenant_id = selected[0].tenant_id if selected else settings.default_tenant_id
    effective_role = selected[0].role if selected else (
        UserRole.admin.value if user.is_platform_admin else "unassigned"
    )
    selected_organization = next((item for item in organization_memberships
                                  if item[0].is_primary), None)
    selected_organization = selected_organization or (
        organization_memberships[0] if organization_memberships else None)
    if selected_organization:
        org_membership, organization = selected_organization
        tenant_id = organization.id
        effective_role = org_membership.role

    current_membership = (
        _membership_to_dict(selected_organization[0], selected_organization[1])
        if selected_organization else None
    )

    await reset_login_failures(login_name)
    await _write_login_audit(
        request, user, "login.succeeded", "success",
        first_default_admin_login=bool(user.is_platform_admin
                                       and user.must_change_password
                                       and user.password_changed_at is None))

    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "tenant_id": tenant_id,
        "tenants": [
            {"id": membership.tenant_id, "name": tenant.name, "role": membership.role}
            for membership, tenant in memberships
        ],
        "organization": ({
            "company_id": selected_organization[1].company_id,
            "organization_id": selected_organization[1].id,
            "membership_id": selected_organization[0].id,
            "organization_level": (selected_organization[1].level.value
                                   if hasattr(selected_organization[1].level, "value")
                                   else str(selected_organization[1].level)),
            "organization_path": selected_organization[1].path,
            "role": selected_organization[0].role,
            "context_version": selected_organization[1].context_version,
            "is_primary": selected_organization[0].is_primary,
        } if selected_organization else None),
        "current_membership": current_membership,
        "organization_memberships": [
            _membership_to_dict(membership, unit)
            for membership, unit in organization_memberships],
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "phone": _mask_phone(user.phone),
            "role": effective_role,
            "must_change_password": user.must_change_password,
            "is_platform_admin": user.is_platform_admin,
            "is_active": user.is_active,
            "password_set": bool(user.password_hash),
            "password_changed_at": user.password_changed_at,
            "profile_incomplete": user.profile_incomplete,
            "version": user.version,
            "tenant_id": tenant_id,
            "company_id": selected_organization[1].company_id if selected_organization else None,
            "organization_id": selected_organization[1].id if selected_organization else None,
            "membership_id": selected_organization[0].id if selected_organization else None,
        },
    }


@router.get("/me", response_model=MeResponse)
async def me(user: AuthUser = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        target = await session.get(User, user.id)
        rows = (await session.execute(
            select(OrganizationMembership, OrganizationUnit)
            .join(OrganizationUnit,
                  OrganizationUnit.id == OrganizationMembership.organization_id)
            .where(OrganizationMembership.user_id == user.id,
                   *_effective_membership_clause(now))
            .order_by(OrganizationMembership.is_primary.desc(),
                      OrganizationUnit.depth, OrganizationMembership.created_at)
        )).all() if target else []
    memberships = [_membership_to_dict(membership, unit) for membership, unit in rows]
    current = next((item for item in memberships if item["id"] == user.membership_id), None)
    organization = ({"id": current["organization_id"],
                     "name": current["organization_name"],
                     "level": current["organization_level"],
                     "company_id": current["company_id"]} if current else None)
    return {"id": user.id, "username": user.username,
            "display_name": target.display_name if target else user.username,
            "phone": _mask_phone(target.phone if target else None),
            "role": user.role, "auth_type": user.auth_type,
            "tenant_id": user.tenant_id or None, "company_id": user.company_id,
            "organization_id": user.organization_id, "membership_id": user.membership_id,
            "organization_level": user.organization_level,
            "must_change_password": target.must_change_password if target else False,
            "is_platform_admin": target.is_platform_admin if target else False,
            "password_set": bool(target and target.password_hash),
            "password_changed_at": target.password_changed_at if target else None,
            "is_active": target.is_active if target else True,
            "profile_incomplete": target.profile_incomplete if target else True,
            "version": target.version if target else 1,
            "organization": organization, "current_membership": current,
            "organization_memberships": memberships}


def _mask_phone(phone: str | None) -> str | None:
    return masked_phone(phone)


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "phone": _mask_phone(user.phone),
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "is_active": user.is_active,
        "is_platform_admin": user.is_platform_admin,
        "can_view_full_phone": user.can_view_full_phone,
        "must_change_password": user.must_change_password,
        "version": user.version,
        "created_at": user.created_at.isoformat() if user.created_at else "",
    }


async def _visible_organization_ids(session, user: AuthUser) -> set[str] | None:
    if user.is_platform_admin or user.auth_type == "dev":
        return None
    context = get_organization_context()
    if not context or context.role != "admin":
        raise HTTPException(status_code=403, detail="User management permission required")
    return await resolve_scope_node_ids(session, context)


@router.get("/users", response_model=UserListResponse)
async def list_users(page: int = 1, page_size: int = 20, query: str | None = None,
                     status: str = "all",
                     user: AuthUser = Depends(require_roles("admin"))):
    if page < 1 or page_size < 1 or page_size > 100 or status not in {"all", "active", "inactive"}:
        raise HTTPException(status_code=422, detail="Invalid pagination or status filter")
    async with async_session() as session:
        visible_ids = await _visible_organization_ids(session, user)
        statement = select(User)
        if visible_ids is not None:
            statement = statement.join(
                OrganizationMembership, OrganizationMembership.user_id == User.id
            ).where(OrganizationMembership.organization_id.in_(visible_ids),
                    OrganizationMembership.active.is_(True)).distinct()
        if query and query.strip():
            term = f"%{query.strip().casefold()}%"
            clauses = [func.lower(User.username).like(term),
                       func.lower(User.display_name).like(term)]
            if re.fullmatch(r"\+?[0-9 -]{6,32}", query.strip()):
                clauses.append(User.phone_hash == phone_lookup_hash(query.strip()))
            statement = statement.where(or_(*clauses))
        if status != "all":
            statement = statement.where(User.is_active.is_(status == "active"))
        total = await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        users = (await session.execute(statement.order_by(
            User.created_at.asc(), User.id).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        current_memberships: dict[str, dict] = {}
        user_ids = [item.id for item in users]
        if user_ids:
            membership_statement = (
                select(OrganizationMembership, OrganizationUnit)
                .join(OrganizationUnit,
                      OrganizationUnit.id == OrganizationMembership.organization_id)
                .where(OrganizationMembership.user_id.in_(user_ids),
                       *_effective_membership_clause(datetime.now(timezone.utc)))
                .order_by(OrganizationMembership.user_id,
                          OrganizationMembership.is_primary.desc(),
                          OrganizationUnit.depth,
                          OrganizationMembership.created_at))
            if visible_ids is not None:
                membership_statement = membership_statement.where(
                    OrganizationMembership.organization_id.in_(visible_ids))
            membership_rows = (await session.execute(membership_statement)).all()
            for membership, unit in membership_rows:
                current_memberships.setdefault(
                    membership.user_id, _membership_to_dict(membership, unit))
    return {"items": [{**_user_to_dict(item),
                        "current_membership": current_memberships.get(item.id)}
                       for item in users], "page": page,
            "page_size": page_size, "total": total,
            "pages": (total + page_size - 1) // page_size}


@router.get("/users/{user_id}")
async def get_user(user_id: str, user: AuthUser = Depends(require_roles("admin"))):
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        visible_ids = await _visible_organization_ids(session, user)
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        statement = (select(OrganizationMembership, OrganizationUnit)
            .join(OrganizationUnit,
                  OrganizationUnit.id == OrganizationMembership.organization_id)
            .where(OrganizationMembership.user_id == user_id,
                   *_effective_membership_clause(now)))
        if visible_ids is not None:
            statement = statement.where(OrganizationMembership.organization_id.in_(visible_ids))
        rows = (await session.execute(statement.order_by(
            OrganizationMembership.is_primary.desc(), OrganizationUnit.depth,
            OrganizationMembership.created_at))).all()
        if visible_ids is not None and not rows:
            raise HTTPException(status_code=404, detail="User not found")
    return {"user": _user_to_dict(target),
            "organization_memberships": [
                _membership_to_dict(membership, unit) for membership, unit in rows]}


@router.get("/users/{user_id}/phone")
async def get_user_phone(user_id: str, request: Request,
                         user: AuthUser = Depends(require_full_phone_access)):
    async with async_session() as session:
        target = await session.get(User, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        _add_user_audit(session, request, user, "user.phone_accessed", target.id,
                        {"reason": "platform_user_management"})
        await session.commit()
        return {"user_id": target.id, "phone": decrypt_phone(target.phone)}


@router.post("/users", status_code=201, response_model=CreateUserResponse)
async def create_user(body: CreateUserRequest, response: Response, request: Request,
                      idempotency_key: str = Header(alias="Idempotency-Key", min_length=1,
                                                    max_length=200),
                      user: AuthUser = Depends(require_roles("admin"))):
    username = body.username.strip()
    display_name = body.display_name.strip()
    job_title = body.job_title.strip()
    phone = body.phone.strip()
    if not display_name or not job_title or not re.fullmatch(r"\+?[0-9 -]{6,32}", phone):
        await _write_user_failure_audit(
            request, user, "user.create_failed", user.id, "PROFILE_VALIDATION")
        raise HTTPException(status_code=422, detail="Invalid user profile fields")
    password = body.password or "111111"
    used_default_password = not body.password
    if not used_default_password:
        _validate_new_password(password)
    role = body.role.value if body.role else "unassigned"
    request_hash = hashlib.sha256(json.dumps(body.model_dump(mode="json"),
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    async with async_session() as session:
        replay = await session.scalar(select(UserCreateIdempotency).where(
            UserCreateIdempotency.actor_id == user.id,
            UserCreateIdempotency.idempotency_key == idempotency_key))
        if replay:
            if replay.request_hash != request_hash:
                await _write_user_failure_audit(
                    request, user, "user.create_failed", user.id, "IDEMPOTENCY_CONFLICT")
                raise HTTPException(status_code=409, detail="Idempotency key payload conflict")
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
            return replay.response_json

        unit = await session.get(OrganizationUnit, body.organization_id)
        if not unit or not unit.active:
            await _write_user_failure_audit(
                request, user, "user.create_failed", user.id, "ORGANIZATION_UNAVAILABLE")
            raise HTTPException(status_code=422, detail="Organization is unavailable")
        context = get_organization_context()
        if not user.is_platform_admin and user.auth_type != "dev":
            if not context or context.role != "admin":
                await _write_user_failure_audit(
                    request, user, "user.create_failed", user.id, "PERMISSION_DENIED")
                raise HTTPException(status_code=403, detail="User management permission required")
            if unit.id not in await resolve_scope_node_ids(session, context):
                await _write_user_failure_audit(
                    request, user, "user.create_failed", user.id, "SCOPE_DENIED")
                raise HTTPException(status_code=403, detail="Organization is outside management scope")

        existing = await session.execute(select(User).where(
            User.username_normalized == normalize_username(username)))
        if existing.scalar_one_or_none():
            await _write_user_failure_audit(
                request, user, "user.create_failed", user.id, "USERNAME_CONFLICT")
            raise HTTPException(status_code=409, detail="Username already exists")
        encrypted_phone, lookup_hash = encrypt_phone(phone)
        new_user = User(
            id=str(uuid.uuid4()),
            username=username,
            display_name=display_name,
            phone=encrypted_phone,
            phone_hash=lookup_hash,
            password_hash=hash_password(password),
            role=UserRole.viewer,  # legacy-only compatibility field
            must_change_password=used_default_password,
            created_by=user.id,
            is_active=True,
        )
        session.add(new_user)
        membership = OrganizationMembership(
            id=str(uuid.uuid4()), user_id=new_user.id, organization_id=unit.id,
            organization_level=unit.level, role=role, job_title=job_title,
            is_primary=True, active=True, created_by=user.id,
        )
        session.add(membership)
        await session.flush()
        payload = {
            "user": _user_to_dict(new_user),
            "primary_membership": _membership_to_dict(membership, unit),
            "used_default_password": used_default_password,
            "must_change_password": used_default_password,
        }
        session.add(UserCreateIdempotency(
            id=str(uuid.uuid4()), actor_id=user.id, idempotency_key=idempotency_key,
            request_hash=request_hash, user_id=new_user.id, response_json=payload,
        ))
        _add_user_audit(session, request, user, "user.created", new_user.id, {
            "target_organization_id": unit.id, "target_membership_id": membership.id,
            "used_default_password": used_default_password, "role": role,
        })
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            replay = await session.scalar(select(UserCreateIdempotency).where(
                UserCreateIdempotency.actor_id == user.id,
                UserCreateIdempotency.idempotency_key == idempotency_key))
            if replay and replay.request_hash == request_hash:
                response.status_code = 200
                response.headers["Idempotent-Replayed"] = "true"
                return replay.response_json
            await _write_user_failure_audit(
                request, user, "user.create_failed", user.id, "DATABASE_CONFLICT")
            raise HTTPException(status_code=409, detail="Username or idempotency key conflict") from exc
    return payload


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
    user: AuthUser = Depends(require_platform_user_admin),
):
    async with async_session() as session:
        if body.is_active is not None:
            await _lock_platform_admin_mutation(session)
        target = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        is_self = target.id == user.id
        removes_admin = body.is_active is False

        if is_self and removes_admin:
            raise HTTPException(
                status_code=400,
                detail="You cannot deactivate or downgrade your own administrator account.",
            )

        if removes_admin and target.is_platform_admin and target.is_active:
            active_admins = await session.execute(
                select(func.count()).select_from(User).where(
                    User.is_platform_admin.is_(True),
                    User.is_active.is_(True),
                )
            )
            if (active_admins.scalar() or 0) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="At least one active administrator is required.",
                )

        if target.version != body.version:
            raise HTTPException(status_code=409, detail="User version conflict")
        if body.display_name is not None:
            target.display_name = body.display_name.strip()
        if body.phone is not None:
            target.phone, target.phone_hash = encrypt_phone(body.phone)
        if body.is_active is not None:
            target.is_active = body.is_active
            if not body.is_active:
                target.token_version += 1
        if body.can_view_full_phone is not None:
            target.can_view_full_phone = body.can_view_full_phone
        target.version += 1
        _add_user_audit(session, request, user,
                        "user.disabled" if body.is_active is False else "user.updated",
                        target.id, {"changed_fields": sorted(body.model_fields_set)})
        await session.commit()
        await session.refresh(target)
    return _user_to_dict(target)


def _validate_new_password(password: str) -> None:
    configured_weak = {item.strip().casefold() for item in
                       settings.password_weak_values.split(",") if item.strip()}
    if (len(password) < 10 or password.casefold() in configured_weak
            or not any(c.isalpha() for c in password)
            or not any(c.isdigit() for c in password)):
        raise HTTPException(status_code=422, detail="New password does not meet policy")


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordRequest,
                          request: Request,
                          user: AuthUser = Depends(get_current_user)):
    if body.new_password != body.confirm_password:
        await _write_user_failure_audit(
            request, user, "user.password_change_failed", user.id, "CONFIRMATION_MISMATCH")
        raise HTTPException(status_code=422, detail="Password confirmation does not match")
    try:
        _validate_new_password(body.new_password)
    except HTTPException:
        await _write_user_failure_audit(
            request, user, "user.password_change_failed", user.id, "PASSWORD_POLICY")
        raise
    async with async_session() as session:
        target = await session.get(User, user.id)
        if not target or not verify_password(body.current_password, target.password_hash):
            await _write_user_failure_audit(
                request, user, "user.password_change_failed", user.id, "CURRENT_PASSWORD")
            raise HTTPException(status_code=400, detail="Current password is invalid")
        if verify_password(body.new_password, target.password_hash):
            await _write_user_failure_audit(
                request, user, "user.password_change_failed", user.id, "PASSWORD_REUSE")
            raise HTTPException(status_code=422, detail="New password must differ")
        target.password_hash = hash_password(body.new_password)
        target.password_changed_at = datetime.now(timezone.utc)
        target.must_change_password = False
        target.token_version += 1
        target.version += 1
        _add_user_audit(session, request, user, "user.password_changed", target.id,
                        {"result": "success"})
        await session.commit()
