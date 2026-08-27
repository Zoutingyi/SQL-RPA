"""Four-level organization and membership invariants."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.schemas import (OrganizationLevel, OrganizationMembership,
                            OrganizationUnit, User)

LEVEL_DEPTH = {
    OrganizationLevel.company: 1,
    OrganizationLevel.department: 2,
    OrganizationLevel.group: 3,
    OrganizationLevel.individual: 4,
}
CHILD_LEVEL = {
    OrganizationLevel.company: OrganizationLevel.department,
    OrganizationLevel.department: OrganizationLevel.group,
    OrganizationLevel.group: OrganizationLevel.individual,
}


class OrganizationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def create_organization_unit(session: AsyncSession, *, name: str,
                                   level: OrganizationLevel | str,
                                   parent_id: str | None = None,
                                   sort_order: int = 0,
                                   pending_confirmation: bool = False) -> OrganizationUnit:
    level = OrganizationLevel(level)
    name = name.strip()
    if not name:
        raise OrganizationError("ORG_NAME_REQUIRED", "Organization name is required")
    unit_id = str(uuid.uuid4())
    if level == OrganizationLevel.company:
        if parent_id is not None:
            raise OrganizationError("ORG_INVALID_PARENT", "Company cannot have a parent")
        company_id, path = unit_id, unit_id
    else:
        if not parent_id:
            raise OrganizationError("ORG_PARENT_REQUIRED", "Parent organization is required")
        parent = await session.scalar(select(OrganizationUnit).where(
            OrganizationUnit.id == parent_id, OrganizationUnit.active.is_(True)))
        if not parent or CHILD_LEVEL.get(parent.level) != level:
            raise OrganizationError("ORG_INVALID_HIERARCHY", "Invalid parent organization level")
        company_id, path = parent.company_id, f"{parent.path}/{unit_id}"
    unit = OrganizationUnit(
        id=unit_id, name=name, level=level, parent_id=parent_id,
        company_id=company_id, path=path, depth=LEVEL_DEPTH[level],
        sort_order=sort_order, pending_confirmation=pending_confirmation, active=True,
    )
    session.add(unit)
    await session.flush()
    return unit


async def create_organization_membership(session: AsyncSession, *, user_id: str,
                                         organization_id: str, role: str = "viewer",
                                         job_title: str | None = None,
                                         valid_from: datetime | None = None,
                                         valid_to: datetime | None = None,
                                         created_by: str | None = None) -> OrganizationMembership:
    user = await session.get(User, user_id)
    unit = await session.get(OrganizationUnit, organization_id)
    if not user or not user.is_active:
        raise OrganizationError("ORG_USER_NOT_FOUND", "Active user not found")
    if not unit or not unit.active:
        raise OrganizationError("ORG_NOT_FOUND", "Active organization not found")
    exists = await session.scalar(select(OrganizationMembership.id).where(
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.organization_id == organization_id))
    if exists:
        raise OrganizationError("ORG_MEMBERSHIP_EXISTS", "Membership already exists")
    now = datetime.now(timezone.utc)
    active_same_level = await session.scalar(select(OrganizationMembership.id).where(
        OrganizationMembership.user_id == user_id,
        OrganizationMembership.organization_level == unit.level,
        OrganizationMembership.active.is_(True),
        (OrganizationMembership.valid_from.is_(None)) |
        (OrganizationMembership.valid_from <= now),
        (OrganizationMembership.valid_to.is_(None)) |
        (OrganizationMembership.valid_to > now)).limit(1))
    membership = OrganizationMembership(
        id=str(uuid.uuid4()), user_id=user_id, organization_id=organization_id,
        organization_level=unit.level, role=role, job_title=job_title,
        is_primary=active_same_level is None, active=True,
        valid_from=valid_from, valid_to=valid_to, created_by=created_by,
    )
    session.add(membership)
    await session.flush()
    return membership


async def set_primary_membership(session: AsyncSession, membership_id: str) -> tuple[str | None, str]:
    membership = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.id == membership_id,
        OrganizationMembership.active.is_(True)).with_for_update())
    if not membership:
        raise OrganizationError("ORG_MEMBERSHIP_NOT_FOUND", "Active membership not found")
    now = datetime.now(timezone.utc)
    if _utc(membership.valid_from) and _utc(membership.valid_from) > now:
        raise OrganizationError("ORG_MEMBERSHIP_INACTIVE", "Membership is not effective yet")
    if _utc(membership.valid_to) and _utc(membership.valid_to) <= now:
        raise OrganizationError("ORG_MEMBERSHIP_INACTIVE", "Membership has expired")
    current = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.user_id == membership.user_id,
        OrganizationMembership.organization_level == membership.organization_level,
        OrganizationMembership.active.is_(True),
        OrganizationMembership.is_primary.is_(True)).with_for_update())
    old_id = current.id if current else None
    if current and current.id != membership.id:
        current.is_primary = False
        current.version += 1
        await session.flush()
    membership.is_primary = True
    membership.version += 1
    await session.flush()
    return old_id, membership.id


async def disable_membership(session: AsyncSession, membership_id: str,
                             replacement_primary_id: str | None = None) -> OrganizationMembership:
    membership = await session.scalar(select(OrganizationMembership).where(
        OrganizationMembership.id == membership_id,
        OrganizationMembership.active.is_(True)).with_for_update())
    if not membership:
        raise OrganizationError("ORG_MEMBERSHIP_NOT_FOUND", "Active membership not found")
    if membership.is_primary:
        now = datetime.now(timezone.utc)
        if not replacement_primary_id:
            others = (await session.execute(select(OrganizationMembership.id).where(
                OrganizationMembership.user_id == membership.user_id,
                OrganizationMembership.organization_level == membership.organization_level,
                OrganizationMembership.active.is_(True),
                (OrganizationMembership.valid_from.is_(None)) |
                (OrganizationMembership.valid_from <= now),
                (OrganizationMembership.valid_to.is_(None)) |
                (OrganizationMembership.valid_to > now),
                OrganizationMembership.id != membership.id))).scalars().all()
            if others:
                raise OrganizationError("ORG_PRIMARY_REPLACEMENT_REQUIRED",
                                        "A replacement primary membership is required")
        else:
            replacement = await session.get(OrganizationMembership, replacement_primary_id)
            if (not replacement or replacement.user_id != membership.user_id or
                    replacement.organization_level != membership.organization_level or
                    not replacement.active or
                    (_utc(replacement.valid_from) and _utc(replacement.valid_from) > now) or
                    (_utc(replacement.valid_to) and _utc(replacement.valid_to) <= now)):
                raise OrganizationError("ORG_INVALID_PRIMARY_REPLACEMENT",
                                        "Replacement membership is invalid")
            membership.is_primary = False
            await session.flush()
            await set_primary_membership(session, replacement.id)
    membership.active = False
    membership.is_primary = False
    membership.version += 1
    await session.flush()
    return membership


async def move_organization_unit(session: AsyncSession, *, unit_id: str,
                                 new_parent_id: str, expected_version: int) -> OrganizationUnit:
    unit = await session.scalar(select(OrganizationUnit).where(
        OrganizationUnit.id == unit_id).with_for_update())
    parent = await session.scalar(select(OrganizationUnit).where(
        OrganizationUnit.id == new_parent_id,
        OrganizationUnit.active.is_(True)).with_for_update())
    if not unit or not parent:
        raise OrganizationError("ORG_NOT_FOUND", "Organization not found")
    # OrganizationUnit uses context_version as its optimistic/path version.
    if unit.context_version != expected_version:
        raise OrganizationError("ORG_VERSION_CONFLICT", "Organization version changed")
    if unit.level == OrganizationLevel.company or CHILD_LEVEL.get(parent.level) != unit.level:
        raise OrganizationError("ORG_INVALID_HIERARCHY", "Invalid target parent level")
    if parent.company_id != unit.company_id:
        raise OrganizationError("ORG_CROSS_COMPANY_MOVE", "Cross-company move is forbidden")
    if parent.path == unit.path or parent.path.startswith(f"{unit.path}/"):
        raise OrganizationError("ORG_CYCLE", "Organization cannot move below itself")
    old_path = unit.path
    new_path = f"{parent.path}/{unit.id}"
    descendants = (await session.execute(select(OrganizationUnit).where(
        OrganizationUnit.company_id == unit.company_id,
        OrganizationUnit.path.startswith(f"{old_path}/")).with_for_update())).scalars().all()
    unit.parent_id = parent.id
    unit.path = new_path
    unit.depth = parent.depth + 1
    unit.context_version += 1
    for descendant in descendants:
        suffix = descendant.path[len(old_path):]
        descendant.path = f"{new_path}{suffix}"
        descendant.depth = unit.depth + suffix.count("/")
        descendant.context_version += 1
    await session.flush()
    return unit
