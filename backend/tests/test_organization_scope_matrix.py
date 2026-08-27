"""Four-level visibility, horizontal isolation and moonlighting isolation matrix."""
import uuid

import pytest


@pytest.mark.parametrize(("selected", "expected"), [
    (0, {0, 1, 2, 3}),
    (1, {1, 2, 3}),
    (2, {2, 3}),
    (3, {3}),
])
@pytest.mark.asyncio
async def test_each_level_sees_itself_and_descendants(init_rag_db, selected, expected):
    from auth import hash_password
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_context import resolve_organization_context, resolve_scope_node_ids
    from organization_service import create_organization_membership, create_organization_unit

    user = User(id=str(uuid.uuid4()), username=f"level-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("Level-Password-123"),
                role=UserRole.admin, is_active=True)
    async with async_session() as session:
        session.add(user)
        company = await create_organization_unit(session, name=f"Level-{uuid.uuid4()}", level="company")
        department = await create_organization_unit(session, name="Department", level="department", parent_id=company.id)
        group = await create_organization_unit(session, name="Group", level="group", parent_id=department.id)
        person = await create_organization_unit(session, name="Person", level="individual", parent_id=group.id)
        nodes = [company, department, group, person]
        memberships = [await create_organization_membership(
            session, user_id=user.id, organization_id=node.id, role="admin") for node in nodes]
        await session.commit()
        context = await resolve_organization_context(
            session, user_id=user.id, organization_id=nodes[selected].id,
            membership_id=memberships[selected].id)
        visible = await resolve_scope_node_ids(session, context)
    assert visible == {nodes[index].id for index in expected}


@pytest.mark.asyncio
async def test_four_level_scope_matrix_and_cross_company_isolation(init_rag_db):
    from auth import hash_password
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_context import resolve_organization_context, resolve_scope_node_ids
    from organization_service import create_organization_membership, create_organization_unit

    user = User(id=str(uuid.uuid4()), username=f"scope-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("Scope-Password-123"),
                role=UserRole.admin, is_active=True)
    async with async_session() as session:
        session.add(user)
        company_a = await create_organization_unit(session, name=f"A-{uuid.uuid4()}", level="company")
        dept_a = await create_organization_unit(session, name="A-Dept", level="department", parent_id=company_a.id)
        dept_sibling = await create_organization_unit(session, name="A-Sibling", level="department", parent_id=company_a.id)
        group_a = await create_organization_unit(session, name="A-Group", level="group", parent_id=dept_a.id)
        person_a = await create_organization_unit(session, name="A-Person", level="individual", parent_id=group_a.id)
        company_b = await create_organization_unit(session, name=f"B-{uuid.uuid4()}", level="company")
        dept_b = await create_organization_unit(session, name="B-Dept", level="department", parent_id=company_b.id)
        nodes = [company_a, dept_a, group_a, person_a, dept_sibling, company_b, dept_b]
        memberships = {}
        for node in nodes:
            memberships[node.id] = await create_organization_membership(
                session, user_id=user.id, organization_id=node.id, role="admin")
        await session.commit()

    expected = {
        company_a.id: {company_a.id, dept_a.id, group_a.id, person_a.id, dept_sibling.id},
        dept_a.id: {dept_a.id, group_a.id, person_a.id},
        group_a.id: {group_a.id, person_a.id},
        person_a.id: {person_a.id},
        dept_sibling.id: {dept_sibling.id},
        company_b.id: {company_b.id, dept_b.id},
    }
    async with async_session() as session:
        for node_id, visible in expected.items():
            context = await resolve_organization_context(
                session, user_id=user.id, organization_id=node_id,
                membership_id=memberships[node_id].id)
            assert await resolve_scope_node_ids(session, context) == visible
        # The same account's concurrent/secondary assignment does not widen a context.
        sibling_context = await resolve_organization_context(
            session, user_id=user.id, organization_id=dept_sibling.id,
            membership_id=memberships[dept_sibling.id].id)
        assert dept_a.id not in await resolve_scope_node_ids(session, sibling_context)
        company_b_context = await resolve_organization_context(
            session, user_id=user.id, organization_id=company_b.id,
            membership_id=memberships[company_b.id].id)
        assert company_a.id not in await resolve_scope_node_ids(session, company_b_context)


@pytest.mark.asyncio
async def test_disabled_or_ineffective_membership_cannot_be_primary(init_rag_db):
    from datetime import datetime, timedelta, timezone
    from auth import hash_password
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_service import (OrganizationError, create_organization_membership,
                                      create_organization_unit, disable_membership,
                                      set_primary_membership)

    user = User(id=str(uuid.uuid4()), username=f"primary-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("Primary-Password-123"),
                role=UserRole.admin, is_active=True)
    async with async_session() as session:
        session.add(user)
        company = await create_organization_unit(session, name=f"Primary-{uuid.uuid4()}", level="company")
        first = await create_organization_membership(session, user_id=user.id,
                                                       organization_id=company.id)
        future_company = await create_organization_unit(session, name=f"Future-{uuid.uuid4()}", level="company")
        future = await create_organization_membership(
            session, user_id=user.id, organization_id=future_company.id,
            valid_from=datetime.now(timezone.utc) + timedelta(days=1))
        await session.commit()
    async with async_session() as session:
        with pytest.raises(OrganizationError) as exc:
            await set_primary_membership(session, future.id)
        assert exc.value.code == "ORG_MEMBERSHIP_INACTIVE"
        with pytest.raises(OrganizationError) as exc:
            await disable_membership(session, first.id, future.id)
        assert exc.value.code == "ORG_INVALID_PRIMARY_REPLACEMENT"
        await session.rollback()
    async with async_session() as session:
        disabled = await disable_membership(session, future.id)
        await session.commit()
        assert disabled.active is False and disabled.is_primary is False
        with pytest.raises(OrganizationError):
            await set_primary_membership(session, future.id)
