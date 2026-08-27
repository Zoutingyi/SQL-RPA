import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from auth import AuthUser, get_current_user, require_roles
from models.database import async_session
from models.schemas import Membership, Tenant, TenantDatabaseConfig, User
from config import get_active_encryption_key
from utils.crypto import encrypt_if_needed

router = APIRouter(prefix="/api/tenants", tags=["tenants"])

class TenantInput(BaseModel):
    name: str

class MembershipInput(BaseModel):
    user_id: str
    role: str = "viewer"

class TenantDatabaseInput(BaseModel):
    db_type: str
    host: str = ""
    port: int = Field(0, ge=0, le=65535)
    database: str
    username: str = ""
    password: str = ""
    pool_size: int = Field(5, ge=1, le=50)

@router.get("")
async def list_tenants(user: AuthUser = Depends(get_current_user)):
    async with async_session() as session:
        rows = (await session.execute(select(Tenant).join(
            Membership, Membership.tenant_id == Tenant.id).where(
            Membership.user_id == user.id, Membership.active.is_(True), Tenant.active.is_(True)
        ))).scalars().all()
    return {"items": [{"id": row.id, "name": row.name} for row in rows]}

@router.post("", status_code=201)
async def create_tenant(body: TenantInput, user: AuthUser = Depends(require_roles("admin"))):
    if not body.name.strip():
        raise HTTPException(422, "Tenant name is required")
    tenant = Tenant(id=str(uuid.uuid4()), name=body.name.strip(), active=True)
    async with async_session() as session:
        session.add(tenant)
        session.add(Membership(id=str(uuid.uuid4()), tenant_id=tenant.id,
                               user_id=user.id, role="admin", active=True))
        await session.commit()
    return {"id": tenant.id, "name": tenant.name}

@router.get("/{tenant_id}/members")
async def list_members(tenant_id: str, user: AuthUser = Depends(require_roles("admin"))):
    if tenant_id != user.tenant_id:
        raise HTTPException(403, "Tenant context mismatch")
    async with async_session() as session:
        rows = (await session.execute(select(Membership).where(
            Membership.tenant_id == tenant_id, Membership.active.is_(True)))).scalars().all()
    return {"items": [{"user_id": row.user_id, "role": row.role} for row in rows]}

@router.put("/{tenant_id}/members")
async def set_member(tenant_id: str, body: MembershipInput,
                     user: AuthUser = Depends(require_roles("admin"))):
    if tenant_id != user.tenant_id or body.role not in {"viewer", "operator", "approver", "admin"}:
        raise HTTPException(403, "Invalid tenant or role")
    async with async_session() as session:
        target_user = await session.get(User, body.user_id)
        if not target_user or not target_user.is_active:
            raise HTTPException(404, "Active user not found")
        row = await session.scalar(select(Membership).where(
            Membership.tenant_id == tenant_id, Membership.user_id == body.user_id))
        if not row:
            row = Membership(id=str(uuid.uuid4()), tenant_id=tenant_id,
                             user_id=body.user_id, role=body.role, active=True)
            session.add(row)
        else:
            row.role, row.active = body.role, True
        await session.commit()
    return {"tenant_id": tenant_id, "user_id": body.user_id, "role": body.role}

@router.put("/{tenant_id}/database")
async def set_tenant_database(tenant_id: str, body: TenantDatabaseInput,
                              user: AuthUser = Depends(require_roles("admin"))):
    if tenant_id != user.tenant_id or body.db_type.lower() not in {"sqlite", "mysql", "postgresql", "postgres"}:
        raise HTTPException(403, "Invalid tenant or database type")
    if not body.database.strip():
        raise HTTPException(422, "database is required")
    version, secret = get_active_encryption_key()
    encrypted = encrypt_if_needed(body.password, secret, version) if body.password else ""
    async with async_session() as session:
        row = await session.get(TenantDatabaseConfig, tenant_id)
        values = body.model_dump(exclude={"password"})
        if not row:
            row = TenantDatabaseConfig(tenant_id=tenant_id, encrypted_password=encrypted, **values)
            session.add(row)
        else:
            for key, value in values.items(): setattr(row, key, value)
            if body.password: row.encrypted_password = encrypted
            row.config_version += 1
        await session.commit()
    from db_connector.factory import close_tenant_connector
    await close_tenant_connector(tenant_id)
    return {"tenant_id": tenant_id, "db_type": body.db_type, "database": body.database,
            "host": body.host, "port": body.port, "pool_size": body.pool_size}
