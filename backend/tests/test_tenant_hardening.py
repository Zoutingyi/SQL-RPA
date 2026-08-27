"""Regression tests for tenant boundaries in browser, Agent, membership and billing."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select


@pytest_asyncio.fixture
async def client(test_rpa_db, init_rag_db):
    from main import app
    app.state.testing = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def _identity(tenant_roles: list[tuple[str, str]], username_prefix="tenant-user"):
    from auth import create_access_token, hash_password
    from models.database import async_session
    from models.schemas import Membership, Tenant, User, UserRole
    user = User(id=str(uuid.uuid4()), username=f"{username_prefix}-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("Password-123"), role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add(user)
        for tenant_id, role in tenant_roles:
            if not await session.get(Tenant, tenant_id):
                session.add(Tenant(id=tenant_id, name=f"Tenant {tenant_id}"))
            session.add(Membership(id=str(uuid.uuid4()), tenant_id=tenant_id,
                                   user_id=user.id, role=role, active=True))
        await session.commit()
    return user, create_access_token(user)


@pytest.mark.asyncio
async def test_database_browser_requires_explicit_tenant_and_isolates_connections(client, tmp_path, monkeypatch):
    from config import settings
    from db_connector.sqlite_impl import SqliteConnector
    from main import app
    from models.database import async_session
    from models.schemas import TenantDatabaseConfig
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    user, token = await _identity([(tenant_a, "viewer"), (tenant_b, "viewer")], "browser")
    paths = {tenant_a: tmp_path / "a.db", tenant_b: tmp_path / "b.db"}
    for tenant_id, path in paths.items():
        conn = SqliteConnector(str(path))
        await conn.connect()
        await conn.execute(f"CREATE TABLE marker_{'a' if tenant_id == tenant_a else 'b'} (id INTEGER)")
        await conn.close()
    async with async_session() as session:
        session.add_all([TenantDatabaseConfig(tenant_id=tid, db_type="sqlite", database=str(path),
                                               pool_size=5) for tid, path in paths.items()])
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "global-key")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        bearer = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/api/db_operations/status", headers=bearer)).status_code == 400
        assert (await client.get("/api/db_operations/tables", headers={
            **bearer, "X-Tenant-ID": tenant_a})).json()[0]["name"] == "marker_a"
        assert (await client.get("/api/db_operations/tables", headers={
            **bearer, "X-Tenant-ID": tenant_b})).json()[0]["name"] == "marker_b"
        assert (await client.get("/api/db_operations/status", headers={
            "Authorization": "Bearer global-key", "X-Tenant-ID": tenant_a})).status_code == 401
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_agent_document_tools_and_keyword_search_are_tenant_scoped(init_rag_db):
    from agent.tools import GetDocumentInfoTool, ListDocumentsTool
    from auth import reset_tenant_id, set_tenant_id
    from models.database import async_session
    from models.schemas import Document, DocStatus
    from textdb.fts5_impl import Fts5TextDB
    from config import settings
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session() as session:
        session.add_all([
            Document(id=doc_a, tenant_id=tenant_a, owner_id="a", filename="a.txt",
                     file_hash=uuid.uuid4().hex, file_size=1, file_type="txt", status=DocStatus.ready),
            Document(id=doc_b, tenant_id=tenant_b, owner_id="b", filename="b.txt",
                     file_hash=uuid.uuid4().hex, file_size=1, file_type="txt", status=DocStatus.ready),
        ])
        await session.commit()
    textdb = Fts5TextDB(settings.database_url)
    token = set_tenant_id(tenant_a)
    try:
        await textdb.insert(str(uuid.uuid4()), doc_a, "tenant boundary keyword")
    finally:
        reset_tenant_id(token)
    token = set_tenant_id(tenant_b)
    try:
        listed = await ListDocumentsTool().execute()
        assert {item["id"] for item in listed.data["documents"]} == {doc_b}
        assert not (await GetDocumentInfoTool().execute(document_id=doc_a)).success
        assert await textdb.search("boundary") == []
    finally:
        reset_tenant_id(token)


@pytest.mark.asyncio
async def test_tenant_admin_only_changes_membership_role(client, monkeypatch):
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import Membership, User
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    admin, admin_token = await _identity([(tenant_a, "admin")], "member-admin")
    target, _ = await _identity([(tenant_a, "viewer"), (tenant_b, "approver")], "member-target")
    original_hash, original_role, original_active = target.password_hash, target.role, target.is_active
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers = {"Authorization": f"Bearer {admin_token}", "X-Tenant-ID": tenant_a}
    try:
        changed = await client.put(f"/api/tenants/{tenant_a}/members", headers=headers,
                                   json={"user_id": target.id, "role": "operator"})
        assert changed.status_code == 200
        assert (await client.patch(f"/api/auth/users/{target.id}", headers=headers,
                                   json={"role": "admin", "password": "Changed-123",
                                         "is_active": False})).status_code == 403
    finally:
        app.state.testing = True
    async with async_session() as session:
        refreshed = await session.get(User, target.id)
        memberships = (await session.execute(select(Membership).where(
            Membership.user_id == target.id))).scalars().all()
    roles = {row.tenant_id: row.role for row in memberships}
    assert roles == {tenant_a: "operator", tenant_b: "approver"}
    assert (refreshed.password_hash, refreshed.role, refreshed.is_active) == (
        original_hash, original_role, original_active)


@pytest.mark.asyncio
async def test_billing_keys_and_concurrent_invoices_are_tenant_scoped(client, monkeypatch):
    from config import settings
    from main import app
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    user, token = await _identity([(tenant_a, "admin"), (tenant_b, "admin")], "billing-tenant")
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    now = datetime.now(timezone.utc)
    body = {"period_start": (now - timedelta(minutes=1)).isoformat(),
            "period_end": (now + timedelta(minutes=1)).isoformat(), "user_id": user.id}
    headers_a = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_a}
    headers_b = {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant_b}
    try:
        a1, a2, b1, b2 = await asyncio.gather(
            client.post("/api/billing/invoices", headers=headers_a, json=body),
            client.post("/api/billing/invoices", headers=headers_a, json=body),
            client.post("/api/billing/invoices", headers=headers_b, json=body),
            client.post("/api/billing/invoices", headers=headers_b, json=body),
        )
        assert all(response.status_code == 201 for response in (a1, a2, b1, b2))
        assert a1.json()["id"] == a2.json()["id"]
        assert b1.json()["id"] == b2.json()["id"]
        assert a1.json()["id"] != b1.json()["id"]
        raw_key = f"same-{uuid.uuid4()}"
        pay_a, pay_b = await asyncio.gather(
            client.post(f"/api/billing/invoices/{a1.json()['id']}/payments",
                        headers={**headers_a, "Idempotency-Key": raw_key}, json={}),
            client.post(f"/api/billing/invoices/{b1.json()['id']}/payments",
                        headers={**headers_b, "Idempotency-Key": raw_key}, json={}),
        )
        assert pay_a.status_code == pay_b.status_code == 201
        assert pay_a.json()["id"] != pay_b.json()["id"]
        assert (await client.get(f"/api/billing/invoices/{a1.json()['id']}",
                                 headers=headers_b)).status_code == 404
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_organization_context_rejects_mismatched_legacy_tenant_header(client, monkeypatch):
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_service import create_organization_membership, create_organization_unit
    user = User(id=str(uuid.uuid4()), username=f"org-header-{uuid.uuid4().hex[:8]}",
                password_hash=hash_password("Password-123"), role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add(user)
        company = await create_organization_unit(session, name=f"Header-{uuid.uuid4()}", level="company")
        department = await create_organization_unit(session, name="Department", level="department",
                                                     parent_id=company.id)
        membership = await create_organization_membership(
            session, user_id=user.id, organization_id=department.id, role="viewer")
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        response = await client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {create_access_token(user)}",
            "X-Organization-ID": department.id,
            "X-Membership-ID": membership.id,
            "X-Tenant-ID": str(uuid.uuid4()),
            "X-Request-ID": "tenant-mismatch-request",
        })
        assert response.status_code == 403
        async with async_session() as session:
            from models.schemas import DomainEvent
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.event_type == "security.organization_tenant_mismatch",
                DomainEvent.aggregate_id == user.id))
            assert event is not None
            assert event.payload["request_id"] == "tenant-mismatch-request"
            assert event.payload["result"] == "denied"
            assert event.payload["source_ip"] is not None
    finally:
        app.state.testing = True
