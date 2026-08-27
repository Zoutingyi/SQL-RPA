import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client(test_rpa_db, init_rag_db):
    from main import app
    app.state.testing = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


@pytest.mark.asyncio
async def test_concurrent_invoice_and_payment_are_idempotent(client):
    now = datetime.now(timezone.utc)
    invoice_body = {
        "period_start": (now - timedelta(minutes=1)).isoformat(),
        "period_end": (now + timedelta(minutes=1)).isoformat(),
        "user_id": f"billing-{uuid.uuid4()}",
    }
    first, second = await asyncio.gather(
        client.post("/api/billing/invoices", json=invoice_body),
        client.post("/api/billing/invoices", json=invoice_body),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    invoice_id = first.json()["id"]
    key = str(uuid.uuid4())
    first, second = await asyncio.gather(
        client.post(f"/api/billing/invoices/{invoice_id}/payments",
                    headers={"Idempotency-Key": key}, json={}),
        client.post(f"/api/billing/invoices/{invoice_id}/payments",
                    headers={"Idempotency-Key": key}, json={}),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_notification_delivery_has_single_atomic_claim(init_rag_db, monkeypatch):
    from models.database import async_session
    from models.schemas import Notification, NotificationDelivery, NotificationEndpoint
    import notifications

    endpoint_id, notification_id, delivery_id = (str(uuid.uuid4()) for _ in range(3))
    async with async_session() as session:
        await session.execute(__import__("sqlalchemy").delete(NotificationDelivery))
        session.add(NotificationEndpoint(id=endpoint_id, kind="webhook",
                                         target="https://public.example/hook", enabled=True))
        session.add(Notification(id=notification_id, event_type="review.completed",
                                 title="done", body="done", payload={}))
        session.add(NotificationDelivery(id=delivery_id, notification_id=notification_id,
                                         endpoint_id=endpoint_id, status="pending"))
        await session.commit()

    calls = 0
    class Response:
        def raise_for_status(self):
            return None
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(notifications, "resolve_public_target",
                        lambda target: ("https://203.0.113.1/hook", {"Host": "public.example"}, {}))
    monkeypatch.setattr(notifications.httpx, "AsyncClient", Client)
    await asyncio.gather(notifications.deliver_pending(), notifications.deliver_pending())
    assert calls == 1
    async with async_session() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.status == "sent" and row.claimed_at is None


def test_ssrf_rejects_private_or_mixed_dns(monkeypatch):
    import notifications
    monkeypatch.setattr(notifications.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(ValueError, match="public"):
        notifications.resolve_public_target("https://rebind.example/hook")


@pytest.mark.asyncio
async def test_approver_cannot_read_global_usage(client, monkeypatch):
    from auth import create_access_token
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole
    user = User(id=str(uuid.uuid4()), username=f"approver-{uuid.uuid4().hex[:8]}",
                password_hash="unused", role=UserRole.approver, is_active=True)
    async with async_session() as session:
        session.add(user); await session.commit()
    token = create_access_token(user)
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    app.state.testing = False
    try:
        response = await client.get("/api/usage/overview",
                                    headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_login_returns_first_accessible_tenant(client, monkeypatch):
    from auth import hash_password
    from config import settings
    from models.database import async_session
    from models.schemas import Membership, Tenant, User, UserRole
    user_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    username = f"login-{uuid.uuid4().hex[:8]}"
    async with async_session() as session:
        session.add_all([
            User(id=user_id, username=username, password_hash=hash_password("Password-123"),
                 role=UserRole.viewer, is_active=True),
            Tenant(id=tenant_id, name=f"Login Tenant {tenant_id}"),
            Membership(id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
                       role="operator", active=True),
        ])
        await session.commit()
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    response = await client.post("/api/auth/login", json={
        "username": username, "password": "Password-123",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"] == tenant_id
    assert payload["user"]["tenant_id"] == tenant_id
    assert payload["user"]["role"] == "operator"
    assert payload["tenants"] == [{
        "id": tenant_id, "name": f"Login Tenant {tenant_id}", "role": "operator",
    }]


@pytest.mark.asyncio
async def test_tenant_target_databases_are_isolated(tmp_path, monkeypatch):
    from auth import reset_tenant_id, set_tenant_id
    from config import settings
    from db_connector.factory import close_connector, get_connector
    from models.database import async_session
    from models.schemas import Tenant, TenantDatabaseConfig
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session() as session:
        session.add_all([
            Tenant(id=tenant_a, name=f"DB-A-{tenant_a}"),
            Tenant(id=tenant_b, name=f"DB-B-{tenant_b}"),
            TenantDatabaseConfig(tenant_id=tenant_a, db_type="sqlite",
                                 database=str(tmp_path / "tenant-a.db"), pool_size=5),
            TenantDatabaseConfig(tenant_id=tenant_b, db_type="sqlite",
                                 database=str(tmp_path / "tenant-b.db"), pool_size=5),
        ])
        await session.commit()
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    await close_connector()
    token_a = set_tenant_id(tenant_a)
    try:
        conn_a = await get_connector()
        await conn_a.execute("CREATE TABLE secrets (value TEXT)")
        await conn_a.execute("INSERT INTO secrets(value) VALUES ('tenant-a')")
    finally:
        reset_tenant_id(token_a)
    token_b = set_tenant_id(tenant_b)
    try:
        conn_b = await get_connector()
        assert "secrets" not in await conn_b.get_tables()
        assert conn_a is not conn_b
    finally:
        reset_tenant_id(token_b)
        await close_connector()


@pytest.mark.asyncio
async def test_conversation_owner_is_enforced(client, monkeypatch):
    from auth import create_organization_context_token
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_context import resolve_organization_context
    from organization_service import create_organization_membership, create_organization_unit
    users = [User(id=str(uuid.uuid4()), username=f"viewer-{uuid.uuid4().hex[:8]}",
                  password_hash="unused", role=UserRole.viewer, is_active=True) for _ in range(2)]
    async with async_session() as session:
        session.add_all(users)
        company = await create_organization_unit(
            session, name=f"Owner-{uuid.uuid4()}", level="company")
        departments = [await create_organization_unit(
            session, name=f"Owner-Dept-{uuid.uuid4()}", level="department",
            parent_id=company.id) for _ in users]
        groups = [await create_organization_unit(
            session, name=f"Owner-Group-{uuid.uuid4()}", level="group",
            parent_id=department.id) for department in departments]
        individuals = [await create_organization_unit(
            session, name=f"Owner-Person-{uuid.uuid4()}", level="individual",
            parent_id=group.id) for group in groups]
        memberships = [await create_organization_membership(
            session, user_id=item.id, organization_id=individual.id,
            role="viewer") for item, individual in zip(users, individuals)]
        await session.commit()
        contexts = [await resolve_organization_context(
                session, user_id=item.id, organization_id=individual.id,
                membership_id=membership.id)
                for item, individual, membership in zip(users, individuals, memberships)]
    tokens = [create_organization_context_token(item, context)
              for item, context in zip(users, contexts)]
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "llm_api_key", "configured-for-ownership-check")
    app.state.testing = False
    try:
        created = await client.post("/api/conversations", json={"title": "private"},
            headers={"Authorization": f"Bearer {tokens[0]}"})
        conversation_id = created.json()["id"]
        headers = {"Authorization": f"Bearer {tokens[1]}"}
        assert (await client.get(f"/api/conversations/{conversation_id}/messages",
                                 headers=headers)).status_code == 404
        assert (await client.patch(f"/api/conversations/{conversation_id}",
                                   headers=headers, json={"title": "stolen"})).status_code == 404
        assert (await client.delete(f"/api/conversations/{conversation_id}",
                                    headers=headers)).status_code == 404
        assert (await client.post("/api/chat", headers=headers, json={
            "conversation_id": conversation_id, "message": "read history"
        })).status_code == 404
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_quota_uses_atomic_reservation(init_rag_db, monkeypatch):
    from api.usage import reserve_user_quota, settle_quota_reservation
    from config import settings
    from fastapi import HTTPException
    from models.database import async_session
    from models.schemas import UsageQuota
    user_id = f"quota-{uuid.uuid4()}"
    async with async_session() as session:
        session.add(UsageQuota(id=str(uuid.uuid4()), user_id=user_id,
                               monthly_token_limit=100, enabled=True))
        await session.commit()
    monkeypatch.setattr(settings, "quota_reservation_tokens", 60)
    monkeypatch.setattr(settings, "quota_reservation_cost_usd", 0.0)
    results = await asyncio.gather(
        reserve_user_quota(user_id, str(uuid.uuid4())),
        reserve_user_quota(user_id, str(uuid.uuid4())),
        return_exceptions=True,
    )
    reservations = [result for result in results if isinstance(result, str)]
    rejected = [result for result in results if isinstance(result, HTTPException)]
    assert len(reservations) == 1 and len(rejected) == 1
    assert rejected[0].status_code == 429
    await settle_quota_reservation(reservations[0])
    assert await reserve_user_quota(user_id, str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_notification_events_are_tenant_scoped(init_rag_db):
    from notifications import publish_notification
    from models.database import async_session
    from models.schemas import Notification, NotificationDelivery, NotificationEndpoint
    tenants = [str(uuid.uuid4()), str(uuid.uuid4())]
    endpoints = [str(uuid.uuid4()), str(uuid.uuid4())]
    async with async_session() as session:
        session.add_all([
            NotificationEndpoint(id=endpoints[index], tenant_id=tenant,
                kind="webhook", target="https://example.com/hook", enabled=True)
            for index, tenant in enumerate(tenants)
        ])
        await session.commit()
    notification_id = await publish_notification("review.completed", {"secret": "a"},
                                                 tenant_id=tenants[0])
    async with async_session() as session:
        note = await session.get(Notification, notification_id)
        deliveries = (await session.execute(__import__("sqlalchemy").select(
            NotificationDelivery).where(NotificationDelivery.notification_id == notification_id)
        )).scalars().all()
    assert note.tenant_id == tenants[0]
    assert len(deliveries) == 1 and deliveries[0].tenant_id == tenants[0]
    assert deliveries[0].endpoint_id == endpoints[0]


@pytest.mark.asyncio
async def test_global_api_key_disabled_in_multitenant_mode(client, monkeypatch):
    from config import settings
    from main import app
    monkeypatch.setattr(settings, "api_key", "legacy-global-key")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        response = await client.get("/api/auth/me", headers={
            "Authorization": "Bearer legacy-global-key", "X-Tenant-ID": "tenant-a"
        })
        assert response.status_code == 401
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_review_tenant_and_assignment_authorization(client, monkeypatch):
    from auth import create_access_token
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import Membership, Tenant, TenantDatabaseConfig, User, UserRole
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    operator = User(id=str(uuid.uuid4()), username=f"op-{uuid.uuid4().hex[:7]}",
                    password_hash="x", role=UserRole.operator, is_active=True)
    approver_a = User(id=str(uuid.uuid4()), username=f"ap-{uuid.uuid4().hex[:7]}",
                      password_hash="x", role=UserRole.approver, is_active=True)
    approver_b = User(id=str(uuid.uuid4()), username=f"bp-{uuid.uuid4().hex[:7]}",
                      password_hash="x", role=UserRole.approver, is_active=True)
    async with async_session() as session:
        session.add_all([Tenant(id=tenant_a, name=f"A-{tenant_a}"),
                         Tenant(id=tenant_b, name=f"B-{tenant_b}"),
                         operator, approver_a, approver_b])
        session.add_all([
            Membership(id=str(uuid.uuid4()), tenant_id=tenant_a, user_id=operator.id, role="operator"),
            Membership(id=str(uuid.uuid4()), tenant_id=tenant_a, user_id=approver_a.id, role="approver"),
            Membership(id=str(uuid.uuid4()), tenant_id=tenant_b, user_id=approver_b.id, role="approver"),
            TenantDatabaseConfig(tenant_id=tenant_a, db_type="sqlite", database=settings.db_sqlite_path,
                                 host="", port=0, username="", encrypted_password="", pool_size=5),
            TenantDatabaseConfig(tenant_id=tenant_b, db_type="sqlite", database=settings.db_sqlite_path,
                                 host="", port=0, username="", encrypted_password="", pool_size=5),
        ])
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers_a = {"Authorization": f"Bearer {create_access_token(operator)}", "X-Tenant-ID": tenant_a}
    headers_b = {"Authorization": f"Bearer {create_access_token(approver_b)}", "X-Tenant-ID": tenant_b}
    try:
        submitted = await client.post("/api/db_operations/submit-review", headers=headers_a,
            json={"sql": "UPDATE users SET age = 39 WHERE id = 2", "reason": "tenant test"})
        assert submitted.status_code == 200
        review_id = submitted.json()["id"]
        assert (await client.get(f"/api/db_operations/review/{review_id}",
                                 headers=headers_b)).status_code == 404
        assert (await client.post(f"/api/db_operations/review/{review_id}/approve",
                                  headers=headers_b, json={})).status_code == 404
        assert (await client.post(f"/api/db_operations/review/{review_id}/actions/transfer",
                                  headers=headers_a, json={"assigned_to": operator.id})).status_code == 403
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_core_resources_are_tenant_scoped(client, monkeypatch):
    from auth import create_access_token
    from config import settings
    from main import app
    from memory.store import MemoryStore
    from models.database import async_session
    from models.schemas import (BackupStatus, BillingInvoice, DbBackup, Document,
                                DocStatus, LlmUsageLog, Membership, Tenant, User, UserRole)
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    admins = [User(id=str(uuid.uuid4()), username=f"ta-{uuid.uuid4().hex[:7]}",
                   password_hash="x", role=UserRole.admin, is_active=True),
              User(id=str(uuid.uuid4()), username=f"tb-{uuid.uuid4().hex[:7]}",
                   password_hash="x", role=UserRole.admin, is_active=True)]
    now = datetime.now(timezone.utc)
    doc_id, invoice_id, backup_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with async_session() as session:
        session.add_all([Tenant(id=tenant_a, name=f"RA-{tenant_a}"),
                         Tenant(id=tenant_b, name=f"RB-{tenant_b}"), *admins])
        session.add_all([
            Membership(id=str(uuid.uuid4()), tenant_id=tenant_a, user_id=admins[0].id, role="admin"),
            Membership(id=str(uuid.uuid4()), tenant_id=tenant_b, user_id=admins[1].id, role="admin"),
            Document(id=doc_id, tenant_id=tenant_a, owner_id=admins[0].id,
                     filename="private.txt", file_hash=uuid.uuid4().hex, file_size=1,
                     file_type="txt", status=DocStatus.ready),
            BillingInvoice(id=invoice_id, tenant_id=tenant_a, invoice_key=str(uuid.uuid4()),
                user_id=admins[0].id, period_start=now, period_end=now + timedelta(days=1),
                currency="USD", subtotal_usd=9, total_usd=9, status="open",
                issued_at=now, due_at=now + timedelta(days=14)),
            DbBackup(id=backup_id, tenant_id=tenant_a, table_name="users",
                operation_type="UPDATE", condition_sql="id = 1", rollback_sql="legacy",
                data_snapshot="{}", status=BackupStatus.active),
            LlmUsageLog(id=str(uuid.uuid4()), tenant_id=tenant_a, user_id=admins[0].id,
                model="tenant-a-secret-model", total_tokens=99, cost_usd=7),
        ])
        await session.commit()
    memory_id = await MemoryStore(tenant_a, admins[0].id).add_memory("tenant A secret")
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers_b = {"Authorization": f"Bearer {create_access_token(admins[1])}",
                 "X-Tenant-ID": tenant_b}
    try:
        documents = (await client.get("/api/documents", headers=headers_b)).json()["items"]
        assert all(item["id"] != doc_id for item in documents)
        assert (await client.delete(f"/api/documents/{doc_id}", headers=headers_b)).status_code == 404
        assert (await client.get(f"/api/billing/invoices/{invoice_id}", headers=headers_b)).status_code == 404
        assert (await client.post(f"/api/db_operations/rollback/{backup_id}", headers=headers_b,
                                  json={"confirm": True})).status_code == 404
        usage = (await client.get("/api/usage/summary", headers=headers_b)).json()
        assert all(item["model"] != "tenant-a-secret-model" for item in usage["items"])
        assert await MemoryStore(tenant_b, admins[1].id).get_memory(memory_id) is None
    finally:
        app.state.testing = True
