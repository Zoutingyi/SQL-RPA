"""Versioned approval-policy evaluation."""

from sqlalchemy import select

from models.database import async_session
from models.schemas import ApprovalPolicy
from auth import get_tenant_id
from organization_context import get_visible_organization_ids


async def evaluate_policy(operation_type: str, table: str, affected_rows: int,
                          sql: str) -> dict:
    scope_ids = await get_visible_organization_ids(get_tenant_id())
    async with async_session() as session:
        policies = (await session.execute(
            select(ApprovalPolicy).where(ApprovalPolicy.enabled.is_(True),
                                         ApprovalPolicy.tenant_id.in_(scope_ids))
            .order_by(ApprovalPolicy.priority, ApprovalPolicy.version.desc())
        )).scalars().all()
    matches = []
    sql_lower = sql.lower()
    for policy in policies:
        if policy.operation_types and operation_type.upper() not in {
            value.upper() for value in policy.operation_types
        }:
            continue
        if policy.tables and table.lower() not in {value.lower() for value in policy.tables}:
            continue
        if affected_rows < policy.min_affected_rows:
            continue
        if policy.sensitive_columns and not any(
            column.lower() in sql_lower for column in policy.sensitive_columns
        ):
            continue
        matches.append(policy)
    if not matches:
        return {"required_approvals": 1, "policy": None, "matches": [],
                "conflicts": [], "reason": "No enabled policy matched; default approval count applied."}
    required = max(policy.required_approvals for policy in matches)
    selected = next(policy for policy in matches if policy.required_approvals == required)
    conflicts = []
    counts = {policy.required_approvals for policy in matches}
    if len(counts) > 1:
        conflicts.append({
            "type": "approval_count_conflict",
            "policy_ids": [policy.id for policy in matches],
            "resolution": "maximum_required_approvals",
        })
    return {
        "required_approvals": required,
        "policy": selected,
        "matches": [{"id": p.id, "name": p.name, "version": p.version,
                     "required_approvals": p.required_approvals} for p in matches],
        "conflicts": conflicts,
        "reason": f"Matched {len(matches)} policy/policies; maximum required approvals is {required}.",
    }
