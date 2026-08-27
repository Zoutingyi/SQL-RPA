import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from auth import AuthUser, require_roles
from organization_context import get_visible_organization_ids
from models.database import async_session
from models.schemas import ApprovalPolicy

router = APIRouter(prefix="/api/approval-policies", tags=["approval-policies"])


class PolicyBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    priority: int = Field(100, ge=0, le=10000)
    operation_types: list[str] = []
    tables: list[str] = []
    sensitive_columns: list[str] = []
    min_affected_rows: int = Field(0, ge=0)
    required_approvals: int = Field(1, ge=1, le=2)


class PolicyEvaluationBody(BaseModel):
    operation_type: str
    table: str
    affected_rows: int = Field(0, ge=0)
    sql: str = ""


def _dict(row: ApprovalPolicy) -> dict:
    return {key: getattr(row, key) for key in (
        "id", "name", "version", "enabled", "priority", "operation_types",
        "tables", "sensitive_columns", "min_affected_rows", "required_approvals",
    )}


@router.get("")
async def list_policies(user: AuthUser = Depends(require_roles("approver"))):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(
            select(ApprovalPolicy).where(ApprovalPolicy.tenant_id.in_(scope_ids))
            .order_by(ApprovalPolicy.priority, ApprovalPolicy.name)
        )).scalars().all()
    return {"items": [_dict(row) for row in rows]}


@router.post("")
async def create_policy(body: PolicyBody, user: AuthUser = Depends(require_roles("admin"))):
    async with async_session() as session:
        row = ApprovalPolicy(id=str(uuid.uuid4()), tenant_id=user.tenant_id,
                             version=1, **body.model_dump())
        session.add(row)
        await session.commit()
        return _dict(row)


@router.put("/{policy_id}")
async def version_policy(policy_id: str, body: PolicyBody,
                         user: AuthUser = Depends(require_roles("admin"))):
    async with async_session() as session:
        old = await session.scalar(select(ApprovalPolicy).where(
            ApprovalPolicy.id == policy_id, ApprovalPolicy.tenant_id == user.tenant_id))
        if not old:
            raise HTTPException(status_code=404, detail="Approval policy not found")
        old.enabled = False
        version = (await session.scalar(
            select(func.max(ApprovalPolicy.version)).where(
                ApprovalPolicy.name == old.name, ApprovalPolicy.tenant_id == user.tenant_id)
        ) or 0) + 1
        row = ApprovalPolicy(id=str(uuid.uuid4()), tenant_id=user.tenant_id,
                             version=version, **body.model_dump())
        session.add(row)
        await session.commit()
        return _dict(row)


@router.post("/{policy_id}/toggle")
async def toggle_policy(policy_id: str, user: AuthUser = Depends(require_roles("admin"))):
    async with async_session() as session:
        row = await session.scalar(select(ApprovalPolicy).where(
            ApprovalPolicy.id == policy_id, ApprovalPolicy.tenant_id == user.tenant_id))
        if not row:
            raise HTTPException(status_code=404, detail="Approval policy not found")
        row.enabled = not row.enabled
        await session.commit()
        return _dict(row)


@router.post("/evaluate")
async def evaluate(body: PolicyEvaluationBody,
                   user: AuthUser = Depends(require_roles("approver"))):
    from approval_policy import evaluate_policy
    result = await evaluate_policy(
        body.operation_type, body.table, body.affected_rows, body.sql
    )
    policy = result.pop("policy")
    return {**result, "selected_policy": _dict(policy) if policy else None}
