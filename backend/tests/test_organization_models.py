"""WP01 organization hierarchy and membership database invariants."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError


@pytest.mark.asyncio
async def test_four_level_hierarchy_and_invalid_parent_rejected(init_rag_db):
    from models.database import async_session
    from models.schemas import OrganizationLevel, OrganizationUnit
    from organization_service import OrganizationError, create_organization_unit
    async with async_session() as session:
        company = await create_organization_unit(session, name=f"Company-{uuid.uuid4()}",
                                                 level=OrganizationLevel.company)
        department = await create_organization_unit(session, name="Research",
            level=OrganizationLevel.department, parent_id=company.id)
        group = await create_organization_unit(session, name="Project",
            level=OrganizationLevel.group, parent_id=department.id)
        individual = await create_organization_unit(session, name="Alice-Manager",
            level=OrganizationLevel.individual, parent_id=group.id)
        await session.commit()
        assert [company.depth, department.depth, group.depth, individual.depth] == [1, 2, 3, 4]
        assert individual.path == f"{company.id}/{department.id}/{group.id}/{individual.id}"

    async with async_session() as session:
        with pytest.raises(OrganizationError) as exc:
            await create_organization_unit(session, name="Invalid", level="group",
                                           parent_id=company.id)
        assert exc.value.code == "ORG_INVALID_HIERARCHY"

    async with async_session() as session:
        session.add(OrganizationUnit(id=str(uuid.uuid4()), name="Bypass", level="group",
            parent_id=company.id, company_id=company.id, path="invalid", depth=3))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_sibling_name_and_individual_child_constraints(init_rag_db):
    from models.database import async_session
    from models.schemas import OrganizationLevel, OrganizationUnit
    from organization_service import OrganizationError, create_organization_unit
    async with async_session() as session:
        company = await create_organization_unit(session, name=f"Unique-{uuid.uuid4()}", level="company")
        await create_organization_unit(session, name="Same", level="department", parent_id=company.id)
        await session.commit()
    async with async_session() as session:
        with pytest.raises(IntegrityError):
            await create_organization_unit(session, name="Same", level="department", parent_id=company.id)
    async with async_session() as session:
        department = await create_organization_unit(session, name="D2", level="department", parent_id=company.id)
        group = await create_organization_unit(session, name="G2", level="group", parent_id=department.id)
        individual = await create_organization_unit(session, name="Person", level="individual", parent_id=group.id)
        await session.commit()
    async with async_session() as session:
        with pytest.raises(OrganizationError):
            await create_organization_unit(session, name="Child", level="individual",
                                           parent_id=individual.id)


@pytest.mark.asyncio
async def test_membership_primary_and_individual_uniqueness(init_rag_db):
    from auth import hash_password
    from models.database import async_session
    from models.schemas import OrganizationMembership, User, UserRole
    from organization_service import (create_organization_membership,
                                      create_organization_unit, set_primary_membership)
    user_a = User(id=str(uuid.uuid4()), username=f"org-a-{uuid.uuid4().hex[:8]}",
                  password_hash=hash_password("Password-123"), role=UserRole.viewer, is_active=True)
    user_b = User(id=str(uuid.uuid4()), username=f"org-b-{uuid.uuid4().hex[:8]}",
                  password_hash=hash_password("Password-123"), role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add_all([user_a, user_b])
        company = await create_organization_unit(session, name=f"Membership-{uuid.uuid4()}", level="company")
        d1 = await create_organization_unit(session, name="D1", level="department", parent_id=company.id)
        d2 = await create_organization_unit(session, name="D2", level="department", parent_id=company.id)
        group = await create_organization_unit(session, name="G", level="group", parent_id=d1.id)
        individual = await create_organization_unit(session, name="P", level="individual", parent_id=group.id)
        first = await create_organization_membership(session, user_id=user_a.id, organization_id=d1.id)
        second = await create_organization_membership(session, user_id=user_a.id, organization_id=d2.id)
        personal = await create_organization_membership(session, user_id=user_a.id,
                                                         organization_id=individual.id)
        await session.commit()
        assert first.is_primary is True and second.is_primary is False and personal.is_primary is True

    async with async_session() as session:
        old_id, new_id = await set_primary_membership(session, second.id)
        await session.commit()
        assert (old_id, new_id) == (first.id, second.id)

    async with async_session() as session:
        session.add(OrganizationMembership(id=str(uuid.uuid4()), user_id=user_b.id,
            organization_id=individual.id, organization_level="individual", role="viewer",
            is_primary=True, active=True))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_organization_context_rejects_mismatched_membership(init_rag_db):
    from auth import hash_password
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_context import resolve_organization_context, resolve_scope_node_ids
    from organization_service import create_organization_membership, create_organization_unit
    user_a = User(id=str(uuid.uuid4()), username=f"ctx-a-{uuid.uuid4().hex[:8]}",
                  password_hash=hash_password("Password-123"), role=UserRole.viewer, is_active=True)
    user_b = User(id=str(uuid.uuid4()), username=f"ctx-b-{uuid.uuid4().hex[:8]}",
                  password_hash=hash_password("Password-123"), role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add_all([user_a, user_b])
        company = await create_organization_unit(session, name=f"Context-{uuid.uuid4()}", level="company")
        department = await create_organization_unit(session, name="Department", level="department",
                                                     parent_id=company.id)
        group = await create_organization_unit(session, name="Group", level="group",
                                               parent_id=department.id)
        member_a = await create_organization_membership(
            session, user_id=user_a.id, organization_id=department.id, role="operator")
        member_b = await create_organization_membership(
            session, user_id=user_b.id, organization_id=department.id, role="viewer")
        await session.commit()
    async with async_session() as session:
        context = await resolve_organization_context(
            session, user_id=user_a.id, organization_id=department.id,
            membership_id=member_a.id)
        assert context.role == "operator" and context.company_id == company.id
        assert await resolve_scope_node_ids(session, context) == {department.id, group.id}
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await resolve_organization_context(
                session, user_id=user_a.id, organization_id=department.id,
                membership_id=member_b.id)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_department_routes_and_login_return_complete_context(init_rag_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from auth import hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_service import create_organization_membership, create_organization_unit

    password = "Department-Password-123"
    user = User(id=str(uuid.uuid4()), username=f"department-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password(password), role=UserRole.admin, is_active=True)
    async with async_session() as session:
        session.add(user)
        company = await create_organization_unit(
            session, name=f"Department-API-{uuid.uuid4()}", level="company")
        department = await create_organization_unit(
            session, name="Engineering", level="department", parent_id=company.id)
        membership = await create_organization_membership(
            session, user_id=user.id, organization_id=company.id, role="admin")
        await session.commit()

    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post("/api/auth/login", json={
                "username": user.username, "password": password})
            assert login.status_code == 200
            body = login.json()
            assert body["organization"]["membership_id"] == membership.id
            assert body["organization"]["context_version"] == 1
            headers = {
                "Authorization": f"Bearer {body['access_token']}",
                "X-Organization-ID": company.id,
                "X-Membership-ID": membership.id,
                "X-Tenant-ID": company.id,
            }
            tree = await client.get("/api/departments/tree", headers=headers)
            assert tree.status_code == 200
            assert {item["id"] for item in tree.json()["items"]} >= {company.id, department.id}
            switched = await client.post("/api/departments/context/switch", headers=headers,
                                         json={"membership_id": membership.id})
            assert switched.status_code == 200
            assert switched.json()["membership_id"] == membership.id
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_connector_cache_isolated_by_membership_and_config_version(init_rag_db,
                                                                         tmp_path, monkeypatch):
    from config import settings
    from db_connector import factory
    from models.database import async_session
    from models.schemas import TenantDatabaseConfig
    from organization_context import OrganizationContext, reset_organization_context, set_organization_context

    organization_id = str(uuid.uuid4())
    async with async_session() as session:
        session.add(TenantDatabaseConfig(
            tenant_id=organization_id, db_type="sqlite",
            database=str(tmp_path / "department-cache.db"), pool_size=5, config_version=1))
        await session.commit()
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    from auth import reset_tenant_id, set_tenant_id
    tenant_token = set_tenant_id(organization_id)
    connectors = []
    membership_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    try:
        for membership_id in membership_ids:
            context = OrganizationContext("user", organization_id, organization_id,
                membership_id, "company", "admin", organization_id, 1, False)
            context_token = set_organization_context(context)
            connectors.append(await factory.get_connector())
            reset_organization_context(context_token)
        assert connectors[0] is not connectors[1]
        async with async_session() as session:
            row = await session.get(TenantDatabaseConfig, organization_id)
            row.config_version = 2
            await session.commit()
        context_token = set_organization_context(OrganizationContext(
            "user", organization_id, organization_id, membership_ids[0], "company", "admin",
            organization_id, 1, False))
        refreshed = await factory.get_connector()
        reset_organization_context(context_token)
        assert refreshed is not connectors[0]
    finally:
        reset_tenant_id(tenant_token)
        await factory.close_tenant_connector(organization_id)
