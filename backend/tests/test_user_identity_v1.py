"""Focused regression tests for the frozen user-api-v1 identity contract."""
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client(test_rpa_db, init_rag_db):
    from main import app
    app.state.testing = True
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as value:
        yield value


def test_username_normalization_is_nfkc_trimmed_and_casefolded():
    from auth import normalize_username
    from models.schemas import User, UserRole

    assert normalize_username("  Admin  ") == "admin"
    assert normalize_username("Ａdmin") == "admin"
    user = User(id="user-1", username="  Example.User ", password_hash="hash",
                role=UserRole.viewer)
    assert user.username == "Example.User"
    assert user.username_normalized == "example.user"


def test_access_token_carries_revocation_and_platform_claims(monkeypatch):
    from auth import create_access_token, decode_access_token
    from config import settings
    from models.schemas import User, UserRole

    monkeypatch.setattr(settings, "secret_key", "identity-test-secret")
    user = User(id="user-2", username="admin", password_hash="hash",
                role=UserRole.admin, token_version=7, is_platform_admin=True)
    payload = decode_access_token(create_access_token(user))
    assert payload["token_version"] == 7
    assert payload["is_platform_admin"] is True


def test_user_create_contract_forbids_legacy_fields_and_defaults_password():
    from pydantic import ValidationError
    from api.auth import CreateUserRequest

    body = CreateUserRequest(username="zhang.san", display_name="张三",
        organization_id="organization-id", job_title="项目经理", phone="13800138000")
    assert body.password is None
    assert body.role is None
    with pytest.raises(ValidationError):
        CreateUserRequest(username="zhang.san", display_name="张三",
            organization_id="organization-id", job_title="项目经理", phone="13800138000",
            tenant_id="legacy")


def test_structured_error_contract_has_no_legacy_detail():
    from api_errors import error_body

    result = error_body(422, [{"loc": ["body", "username"], "msg": "invalid"}], "rid")
    assert set(result) == {"error"}
    assert result["error"]["field_errors"] == {"username": "invalid"}
    assert result["error"]["request_id"] == "rid"


@pytest.mark.asyncio
async def test_platform_user_management_pagination_detail_update_and_audit(client, monkeypatch):
    import uuid
    from sqlalchemy import func, select
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import DomainEvent, User, UserRole

    suffix = uuid.uuid4().hex[:10]
    admin = User(id=str(uuid.uuid4()), username=f"platform-{suffix}",
        display_name="Platform Admin", phone="13800138000",
        password_hash=hash_password("Platform-123"), role=UserRole.admin,
        is_platform_admin=True, must_change_password=False, is_active=True)
    target = User(id=str(uuid.uuid4()), username=f"managed-{suffix}",
        display_name="Managed User", phone="13900139000",
        password_hash=hash_password("Managed-1234"), role=UserRole.viewer,
        is_active=True)
    async with async_session() as session:
        session.add_all([admin, target])
        await session.commit()
    token = create_access_token(admin)
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers = {"Authorization": f"Bearer {token}"}
    try:
        listed = await client.get("/api/auth/users", headers=headers,
                                  params={"page": 1, "page_size": 1,
                                          "query": target.username, "status": "active"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["phone"] == "139****9000"
        assert "current_membership" in listed.json()["items"][0]
        assert listed.json()["items"][0]["current_membership"] is None

        detail = await client.get(f"/api/auth/users/{target.id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["user"]["id"] == target.id

        changed = await client.patch(f"/api/auth/users/{target.id}", headers=headers,
            json={"display_name": "Updated User", "phone": "13700137000", "version": 1})
        assert changed.status_code == 200
        assert changed.json()["version"] == 2
        assert changed.json()["phone"] == "137****7000"
        async with async_session() as session:
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_id == target.id,
                DomainEvent.event_type == "user.updated"))
            assert event is not None
            assert event.payload["actor_id"] == admin.id
            assert "phone" not in event.payload
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_create_user_exact_idempotent_replay(client, monkeypatch):
    import uuid
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_service import create_organization_unit

    suffix = uuid.uuid4().hex[:10]
    admin = User(id=str(uuid.uuid4()), username=f"creator-{suffix}",
        display_name="Creator", password_hash=hash_password("Creator-1234"),
        role=UserRole.admin, is_platform_admin=True, is_active=True)
    async with async_session() as session:
        session.add(admin)
        company = await create_organization_unit(
            session, name=f"User API {suffix}", level="company")
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers = {"Authorization": f"Bearer {create_access_token(admin)}",
               "Idempotency-Key": f"create-{suffix}"}
    body = {"username": f"new-{suffix}", "display_name": "New User",
            "organization_id": company.id, "job_title": "Engineer",
            "phone": "13600136000", "password": None, "role": None}
    try:
        created = await client.post("/api/auth/users", headers=headers, json=body)
        assert created.status_code == 201
        assert created.json()["used_default_password"] is True
        assert created.json()["primary_membership"]["role"] is None
        assert created.json()["primary_membership"]["job_title"] == "Engineer"
        listed = await client.get("/api/auth/users", headers=headers,
                                  params={"query": body["username"]})
        assert listed.status_code == 200
        current = listed.json()["items"][0]["current_membership"]
        assert current["organization_id"] == company.id
        assert current["job_title"] == "Engineer"
        assert current["role"] is None
        replay = await client.post("/api/auth/users", headers=headers, json=body)
        assert replay.status_code == 200
        assert replay.headers["Idempotent-Replayed"] == "true"
        conflict = await client.post("/api/auth/users", headers=headers,
                                     json={**body, "display_name": "Different"})
        assert conflict.status_code == 409
        from sqlalchemy import select
        from models.schemas import DomainEvent
        async with async_session() as session:
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_id == created.json()["user"]["id"],
                DomainEvent.event_type == "user.created"))
            assert event is not None
            serialized = json.dumps(event.payload).casefold()
            assert "111111" not in serialized
            assert "password_hash" not in serialized
            failed = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_id == admin.id,
                DomainEvent.event_type == "user.create_failed"))
            assert failed is not None
            assert failed.payload["failure_code"] == "IDEMPOTENCY_CONFLICT"
            assert body["username"] not in json.dumps(failed.payload)
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_password_change_revokes_old_token_and_writes_safe_audit(client, monkeypatch):
    import uuid
    from sqlalchemy import select
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import DomainEvent, User, UserRole

    suffix = uuid.uuid4().hex[:10]
    user = User(id=str(uuid.uuid4()), username=f"password-{suffix}",
        display_name="Password User", password_hash=hash_password("Current-1234"),
        role=UserRole.viewer, is_platform_admin=True, is_active=True)
    async with async_session() as session:
        session.add(user)
        await session.commit()
    old_token = create_access_token(user)
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers = {"Authorization": f"Bearer {old_token}"}
    try:
        changed = await client.post("/api/auth/change-password", headers=headers,
            json={"current_password": "Current-1234", "new_password": "NewSecure-5678",
                  "confirm_password": "NewSecure-5678"})
        assert changed.status_code == 204
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
        async with async_session() as session:
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_id == user.id,
                DomainEvent.event_type == "user.password_changed"))
            assert event is not None
            serialized = json.dumps(event.payload).casefold()
            assert "current-1234" not in serialized
            assert "newsecure-5678" not in serialized
            assert "password_hash" not in serialized
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_unassigned_membership_only_allows_identity_and_context_endpoints(
        client, monkeypatch):
    import uuid
    from auth import create_organization_context_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_context import resolve_organization_context
    from organization_service import create_organization_membership, create_organization_unit

    suffix = uuid.uuid4().hex[:10]
    user = User(id=str(uuid.uuid4()), username=f"unassigned-{suffix}",
        display_name="Unassigned", password_hash=hash_password("Unassigned-1234"),
        role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add(user)
        company = await create_organization_unit(
            session, name=f"Unassigned {suffix}", level="company")
        membership = await create_organization_membership(
            session, user_id=user.id, organization_id=company.id,
            role="unassigned", job_title="Pending")
        await session.commit()
        context = await resolve_organization_context(
            session, user_id=user.id, organization_id=company.id,
            membership_id=membership.id)
    token = create_organization_context_token(user, context)
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers = {"Authorization": f"Bearer {token}"}
    try:
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
        assert (await client.get(
            "/api/departments/memberships/me", headers=headers)).status_code == 200
        for path in ("/api/documents", "/api/conversations", "/api/notifications",
                     "/api/db_operations/status", "/api/billing/invoices"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 403, path
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_password_change_has_independent_rate_limit_and_failure_audit(
        client, monkeypatch):
    import uuid
    from sqlalchemy import select
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from middleware import ratelimit
    from models.database import async_session
    from models.schemas import DomainEvent, User, UserRole

    suffix = uuid.uuid4().hex[:10]
    user = User(id=str(uuid.uuid4()), username=f"limited-{suffix}",
        display_name="Limited", password_hash=hash_password("Current-1234"),
        role=UserRole.admin, is_platform_admin=True, is_active=True)
    async with async_session() as session:
        session.add(user)
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    monkeypatch.setitem(ratelimit.SENSITIVE_LIMITS, "/api/auth/change-password", (1, 300))
    app.state.testing = False
    headers = {"Authorization": f"Bearer {create_access_token(user)}"}
    bad_body = {"current_password": "Current-1234", "new_password": "NewSecure-5678",
                "confirm_password": "Different-5678"}
    try:
        assert (await client.post(
            "/api/auth/change-password", headers=headers, json=bad_body)).status_code == 422
        limited = await client.post(
            "/api/auth/change-password", headers=headers, json=bad_body)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "SENSITIVE_RATE_LIMITED"
        async with async_session() as session:
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_id == user.id,
                DomainEvent.event_type == "user.password_change_failed"))
            assert event is not None
            assert event.payload["failure_code"] == "CONFIRMATION_MISMATCH"
            assert "NewSecure-5678" not in json.dumps(event.payload)
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


def test_password_policy_uses_configured_weak_value_list(monkeypatch):
    from fastapi import HTTPException
    from api.auth import _validate_new_password
    from config import settings

    monkeypatch.setattr(settings, "password_weak_values", "CustomWeak-123,admin")
    with pytest.raises(HTTPException) as exc:
        _validate_new_password("CustomWeak-123")
    assert exc.value.status_code == 422
    _validate_new_password("Acceptable-9876")


@pytest.mark.asyncio
async def test_platform_admin_without_membership_can_login_and_restore_complete_me(
        client, monkeypatch):
    import uuid
    from auth import hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole

    suffix = uuid.uuid4().hex[:10]
    user = User(id=str(uuid.uuid4()), username=f"platform-only-{suffix}",
        display_name="Platform Only", phone="13800138000",
        password_hash=hash_password("PlatformOnly-1234"), role=UserRole.admin,
        is_platform_admin=True, must_change_password=False,
        profile_incomplete=False, is_active=True)
    async with async_session() as session:
        session.add(user)
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        login = await client.post("/api/auth/login", json={
            "username": user.username.upper(), "password": "PlatformOnly-1234"})
        assert login.status_code == 200
        payload = login.json()
        assert payload["organization"] is None
        assert payload["current_membership"] is None
        assert payload["organization_memberships"] == []
        assert payload["user"]["is_platform_admin"] is True
        assert payload["user"]["password_changed_at"] is None
        assert payload["user"]["phone"] == "138****8000"
        me = await client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {payload['access_token']}"})
        assert me.status_code == 200
        restored = me.json()
        assert restored["organization"] is None
        assert restored["current_membership"] is None
        assert restored["is_platform_admin"] is True
        assert restored["display_name"] == "Platform Only"
        assert restored["password_set"] is True
        assert (await client.get("/api/documents", headers={
            "Authorization": f"Bearer {payload['access_token']}"})).status_code == 403
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_default_tenant_bootstrap_does_not_grant_membership():
    import uuid
    from sqlalchemy import select
    from auth import ensure_default_tenant, hash_password
    from models.database import async_session
    from models.schemas import Membership, User, UserRole

    user = User(id=str(uuid.uuid4()), username=f"no-default-{uuid.uuid4().hex[:8]}",
                display_name="No Default", password_hash=hash_password("NoDefault-1234"),
                role=UserRole.admin, is_platform_admin=True, is_active=True)
    async with async_session() as session:
        session.add(user)
        await session.commit()
    await ensure_default_tenant()
    async with async_session() as session:
        membership = await session.scalar(select(Membership).where(
            Membership.user_id == user.id))
        assert membership is None
    from auth import create_access_token, decode_access_token
    assert decode_access_token(create_access_token(user))["role"] == "platform_admin"
    user.is_platform_admin = False
    assert decode_access_token(create_access_token(user))["role"] == "unassigned"


@pytest.mark.asyncio
async def test_phone_is_encrypted_and_full_access_is_separately_audited(client, monkeypatch):
    import uuid
    from sqlalchemy import select
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import DomainEvent, User, UserRole
    from user_pii import encrypt_phone

    encrypted, lookup_hash = encrypt_phone("13800138000")
    admin = User(id=str(uuid.uuid4()), username=f"phone-admin-{uuid.uuid4().hex[:8]}",
        display_name="Phone Admin", password_hash=hash_password("PhoneAdmin-1234"),
        role=UserRole.viewer, is_platform_admin=True, can_view_full_phone=True,
        is_active=True)
    target = User(id=str(uuid.uuid4()), username=f"phone-user-{uuid.uuid4().hex[:8]}",
        display_name="Phone User", phone=encrypted, phone_hash=lookup_hash,
        password_hash=hash_password("PhoneUser-1234"), role=UserRole.viewer, is_active=True)
    restricted = User(id=str(uuid.uuid4()), username=f"phone-restricted-{uuid.uuid4().hex[:8]}",
        display_name="Phone Restricted", password_hash=hash_password("Restricted-1234"),
        role=UserRole.viewer, is_platform_admin=True, can_view_full_phone=False,
        is_active=True)
    async with async_session() as session:
        session.add_all([admin, target, restricted])
        await session.commit()
    assert target.phone.startswith("ENC:v") and "13800138000" not in target.phone
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    headers = {"Authorization": f"Bearer {create_access_token(admin)}"}
    try:
        detail = await client.get(f"/api/auth/users/{target.id}", headers=headers)
        assert detail.json()["user"]["phone"] == "138****8000"
        full = await client.get(f"/api/auth/users/{target.id}/phone", headers=headers)
        assert full.status_code == 200 and full.json()["phone"] == "13800138000"
        denied = await client.get(f"/api/auth/users/{target.id}/phone", headers={
            "Authorization": f"Bearer {create_access_token(restricted)}"})
        assert denied.status_code == 403
        async with async_session() as session:
            event = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_id == target.id,
                DomainEvent.event_type == "user.phone_accessed"))
            assert event is not None
            assert "13800138000" not in json.dumps(event.payload)
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_database_revision_and_openapi_contract_are_current():
    from main import app
    from migration_gate import check_database_revision

    revision = await check_database_revision()
    assert revision.ready is True
    schema = app.openapi()
    assert schema["info"]["x-api-contract-version"] == "user-api-v1"
    for path in ("/api/auth/login", "/api/auth/me", "/api/auth/users"):
        assert path in schema["paths"]
    login_schema = schema["paths"]["/api/auth/login"]["post"]["responses"]["200"]
    assert "LoginResponse" in json.dumps(login_schema)


@pytest.mark.asyncio
async def test_login_audit_covers_failure_inactive_lock_and_default_admin(client, monkeypatch):
    import uuid
    from sqlalchemy import select
    from auth import hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import DomainEvent, User, UserRole

    suffix = uuid.uuid4().hex[:8]
    active = User(id=str(uuid.uuid4()), username=f"audit-login-{suffix}",
        display_name="Audit Login", password_hash=hash_password("AuditLogin-1234"),
        role=UserRole.viewer, is_platform_admin=True, must_change_password=True,
        is_active=True)
    inactive = User(id=str(uuid.uuid4()), username=f"inactive-login-{suffix}",
        display_name="Inactive", password_hash=hash_password("Inactive-1234"),
        role=UserRole.viewer, is_active=False)
    async with async_session() as session:
        session.add_all([active, inactive])
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        assert (await client.post("/api/auth/login", json={
            "username": active.username, "password": "wrong"})).status_code == 401
        assert (await client.post("/api/auth/login", json={
            "username": inactive.username, "password": "Inactive-1234"})).status_code == 401
        success = await client.post("/api/auth/login", json={
            "username": active.username, "password": "AuditLogin-1234"})
        assert success.status_code == 200
        locked_name = f"missing-{suffix}"
        for _ in range(5):
            assert (await client.post("/api/auth/login", json={
                "username": locked_name, "password": "never-record-this"})).status_code == 401
        assert (await client.post("/api/auth/login", json={
            "username": locked_name, "password": "never-record-this"})).status_code == 429
        async with async_session() as session:
            events = (await session.execute(select(DomainEvent).where(
                DomainEvent.aggregate_type == "authentication",
                DomainEvent.aggregate_id.in_([active.id, inactive.id])))).scalars().all()
            assert {event.event_type for event in events} >= {"login.failed", "login.succeeded"}
            assert any(event.payload["result"] == "inactive_account" for event in events)
            success_event = next(event for event in events if event.event_type == "login.succeeded")
            assert success_event.payload["first_default_admin_login"] is True
            serialized = json.dumps([event.payload for event in events])
            assert "AuditLogin-1234" not in serialized and "access_token" not in serialized
            locked = await session.scalar(select(DomainEvent).where(
                DomainEvent.aggregate_type == "authentication",
                DomainEvent.event_type == "login.locked"))
            assert locked is not None
            assert "never-record-this" not in json.dumps(locked.payload)
    finally:
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False


@pytest.mark.asyncio
async def test_two_platform_admins_cannot_be_concurrently_disabled(client, monkeypatch):
    import asyncio
    import uuid
    from sqlalchemy import func, select
    from auth import create_access_token, hash_password
    from config import settings
    from main import app
    from models.database import async_session
    from models.schemas import User, UserRole

    suffix = uuid.uuid4().hex[:8]
    first = User(id=str(uuid.uuid4()), username=f"lock-a-{suffix}", display_name="Lock A",
        password_hash=hash_password("LockAdminA-1234"), role=UserRole.viewer,
        is_platform_admin=True, is_active=True)
    second = User(id=str(uuid.uuid4()), username=f"lock-b-{suffix}", display_name="Lock B",
        password_hash=hash_password("LockAdminB-1234"), role=UserRole.viewer,
        is_platform_admin=True, is_active=True)
    async with async_session() as session:
        existing = (await session.execute(select(User).where(
            User.is_platform_admin.is_(True), User.is_active.is_(True)))).scalars().all()
        existing_ids = [item.id for item in existing]
        for item in existing:
            item.is_active = False
        session.add_all([first, second])
        await session.commit()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        responses = await asyncio.gather(
            client.patch(f"/api/auth/users/{second.id}",
                headers={"Authorization": f"Bearer {create_access_token(first)}"},
                json={"is_active": False, "version": 1}),
            client.patch(f"/api/auth/users/{first.id}",
                headers={"Authorization": f"Bearer {create_access_token(second)}"},
                json={"is_active": False, "version": 1}),
        )
        assert sum(response.status_code == 200 for response in responses) == 1
        async with async_session() as session:
            remaining = await session.scalar(select(func.count()).select_from(User).where(
                User.is_platform_admin.is_(True), User.is_active.is_(True)))
            assert remaining == 1
    finally:
        async with async_session() as session:
            restored = (await session.execute(select(User).where(
                User.id.in_(existing_ids)))).scalars().all()
            for item in restored:
                item.is_active = True
            await session.commit()
        app.state.testing = True
        settings.api_key = ""
        settings.multi_tenant_enabled = False
