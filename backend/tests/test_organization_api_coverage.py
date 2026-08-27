"""High-value organization API, service invariant, and context invalidation coverage."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select


async def _seed(role="admin"):
    from auth import create_access_token, hash_password
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_service import create_organization_membership, create_organization_unit

    suffix = uuid.uuid4().hex[:8]
    admin = User(id=str(uuid.uuid4()), username=f"org-api-{suffix}",
                 password_hash=hash_password("Organization-Api-123"),
                 role=UserRole.viewer, is_active=True)
    target = User(id=str(uuid.uuid4()), username=f"org-target-{suffix}",
                  password_hash=hash_password("Organization-Target-123"),
                  role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add_all([admin, target])
        company = await create_organization_unit(session, name=f"Company-{suffix}", level="company")
        d1 = await create_organization_unit(session, name="D1", level="department", parent_id=company.id)
        d2 = await create_organization_unit(session, name="D2", level="department", parent_id=company.id)
        group = await create_organization_unit(session, name="G1", level="group", parent_id=d1.id)
        person = await create_organization_unit(session, name="P1", level="individual", parent_id=group.id)
        admin_membership = await create_organization_membership(
            session, user_id=admin.id, organization_id=company.id, role=role)
        await session.commit()
    return {
        "admin": admin, "target": target, "company": company, "d1": d1, "d2": d2,
        "group": group, "person": person, "membership": admin_membership,
        "token": create_access_token(admin),
    }


def _headers(seed, request_id="org-api-test"):
    return {
        "Authorization": f"Bearer {seed['token']}",
        "X-Organization-ID": seed["company"].id,
        "X-Membership-ID": seed["membership"].id,
        "X-Tenant-ID": seed["company"].id,
        "X-Request-ID": request_id,
    }


@pytest.mark.asyncio
async def test_organization_api_crud_move_disable_and_memberships(init_rag_db, monkeypatch):
    from config import settings
    from main import app

    seed = await _seed()
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = _headers(seed)
            assert (await client.get("/api/departments/tree", headers=headers)).status_code == 200
            assert (await client.get("/api/departments/memberships/me", headers=headers)).status_code == 200
            assert (await client.get(f"/api/departments/{seed['d1'].id}", headers=headers)).status_code == 200

            created = await client.post("/api/departments", headers=headers, json={
                "name": "Disposable", "level": "department", "parent_id": seed["company"].id})
            assert created.status_code == 201
            disposable = created.json()
            updated = await client.put(f"/api/departments/{disposable['id']}", headers=headers,
                                       json={"name": "Disposable Updated", "sort_order": 2,
                                             "version": disposable["version"]})
            assert updated.status_code == 200
            stale = await client.put(f"/api/departments/{disposable['id']}", headers=headers,
                                     json={"name": "Stale", "version": disposable["version"]})
            assert stale.status_code == 409

            moved = await client.post(f"/api/departments/{seed['group'].id}/move", headers=headers,
                                      json={"parent_id": seed["d2"].id,
                                            "version": seed["group"].context_version})
            assert moved.status_code == 200
            assert moved.json()["parent_id"] == seed["d2"].id
            assert (await client.post(f"/api/departments/{disposable['id']}/disable",
                                      headers=headers)).status_code == 200
            assert (await client.post(f"/api/departments/{seed['d2'].id}/disable",
                                      headers=headers)).status_code == 409

            first = await client.post(f"/api/departments/{seed['d1'].id}/memberships",
                                      headers=headers, json={
                                          "user_id": seed["target"].id, "role": "viewer",
                                          "job_title": "Engineer"})
            second = await client.post(f"/api/departments/{seed['d2'].id}/memberships",
                                       headers=headers, json={
                                           "user_id": seed["target"].id, "role": "operator",
                                           "job_title": "Consultant"})
            assert first.status_code == second.status_code == 201
            listed = await client.get(f"/api/departments/{seed['d1'].id}/memberships",
                                      headers=headers)
            assert first.json()["id"] in {item["id"] for item in listed.json()["items"]}
            changed = await client.put(
                f"/api/departments/memberships/{second.json()['id']}", headers=headers,
                json={"role": "approver", "job_title": "Reviewer",
                      "version": second.json()["version"]})
            assert changed.status_code == 200
            stale_member = await client.put(
                f"/api/departments/memberships/{second.json()['id']}", headers=headers,
                json={"role": "viewer", "version": second.json()["version"]})
            assert stale_member.status_code == 409
            primary = await client.post(
                f"/api/departments/memberships/{second.json()['id']}/set-primary",
                headers=headers, json={"reason": "assignment change"})
            assert primary.status_code == 200
            disabled = await client.post(
                f"/api/departments/memberships/{first.json()['id']}/disable",
                headers=headers, json={"reason": "ended"})
            assert disabled.status_code == 200
            switched = await client.post("/api/departments/context/switch", headers=headers,
                                         json={"membership_id": seed["membership"].id})
            assert switched.status_code == 200 and switched.json()["expires_in"] == 900
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_organization_api_permissions_cross_scope_and_conflicts(init_rag_db, monkeypatch):
    from config import settings
    from main import app

    viewer = await _seed("viewer")
    other = await _seed("admin")
    monkeypatch.setattr(settings, "api_key", "auth-enabled")
    monkeypatch.setattr(settings, "multi_tenant_enabled", True)
    app.state.testing = False
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            viewer_headers = _headers(viewer)
            denied = await client.post("/api/departments", headers=viewer_headers, json={
                "name": "Denied", "level": "department", "parent_id": viewer["company"].id})
            assert denied.status_code == 403
            assert (await client.get(f"/api/departments/{other['d1'].id}",
                                     headers=viewer_headers)).status_code == 403
            assert (await client.post("/api/departments/context/switch", headers=viewer_headers,
                                      json={"membership_id": other["membership"].id})).status_code == 403

            admin = await _seed("admin")
            headers = _headers(admin)
            assert (await client.post("/api/departments", headers=headers, json={
                "name": "Nested Company", "level": "company"})).status_code == 403
            assert (await client.post("/api/departments", headers=headers, json={
                "name": "Outside", "level": "department",
                "parent_id": other["company"].id})).status_code == 403
            assert (await client.put(f"/api/departments/{other['d1'].id}", headers=headers,
                                     json={"name": "Outside", "version": 1})).status_code == 403
            assert (await client.put(f"/api/departments/{admin['d1'].id}", headers=headers,
                                     json={"name": "   ", "version": 1})).status_code == 422
            assert (await client.post(f"/api/departments/{other['d1'].id}/memberships",
                                      headers=headers, json={
                                          "user_id": admin["target"].id})).status_code == 403
            assert (await client.get(f"/api/departments/{other['d1'].id}/memberships",
                                     headers=headers)).status_code == 403
            missing_membership = str(uuid.uuid4())
            assert (await client.put(
                f"/api/departments/memberships/{missing_membership}", headers=headers,
                json={"role": "viewer", "version": 1})).status_code == 403
            assert (await client.put(
                f"/api/departments/memberships/{other['membership'].id}", headers=headers,
                json={"role": "viewer", "version": 1})).status_code == 403
            now = datetime.now(timezone.utc)
            assert (await client.put(
                f"/api/departments/memberships/{admin['membership'].id}", headers=headers,
                json={"version": 1, "valid_from": now.isoformat(),
                      "valid_to": (now - timedelta(seconds=1)).isoformat()})).status_code == 422
            assert (await client.post(
                f"/api/departments/memberships/{missing_membership}/set-primary",
                headers=headers, json={"reason": "negative"})).status_code == 403
            assert (await client.post(
                f"/api/departments/memberships/{missing_membership}/disable",
                headers=headers, json={"reason": "negative"})).status_code == 403
            assert (await client.post("/api/departments/context/switch", headers=headers,
                                      json={"membership_id": missing_membership})).status_code == 403
            same = {"name": "Race", "level": "department", "parent_id": admin["company"].id}
            results = await asyncio.gather(
                client.post("/api/departments", headers=headers, json=same),
                client.post("/api/departments", headers=headers, json=same))
            assert sorted(response.status_code for response in results) == [201, 409]
            assert (await client.post(f"/api/departments/{admin['company'].id}/disable",
                                      headers=headers)).status_code == 403
            assert (await client.post(f"/api/departments/{admin['group'].id}/move",
                                      headers=headers, json={
                                          "parent_id": other["d1"].id,
                                          "version": admin["group"].context_version})).status_code == 403
    finally:
        app.state.testing = True


@pytest.mark.asyncio
async def test_context_expiry_path_level_owner_and_write_scope(init_rag_db):
    from models.database import async_session
    from models.schemas import OrganizationMembership, OrganizationUnit
    from organization_context import (
        _effective, current_write_scope, get_resource_scope,
        get_visible_organization_ids, require_resource_scope,
        reset_organization_context, resolve_organization_context,
        set_organization_context,
    )

    seed = await _seed()
    async with async_session() as session:
        membership = await session.get(OrganizationMembership, seed["membership"].id)
        membership.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        with pytest.raises(HTTPException, match="not effective"):
            await resolve_organization_context(session, user_id=seed["admin"].id,
                                               organization_id=seed["company"].id,
                                               membership_id=membership.id)
        membership.valid_to = None
        membership.organization_level = "department"
        await session.commit()
        with pytest.raises(HTTPException, match="level mismatch"):
            await resolve_organization_context(session, user_id=seed["admin"].id,
                                               organization_id=seed["company"].id,
                                               membership_id=membership.id)
        membership.organization_level = "company"
        unit = await session.get(OrganizationUnit, seed["company"].id)
        unit.path = f"missing/{unit.id}"
        await session.commit()
        with pytest.raises(HTTPException, match="path is invalid"):
            await resolve_organization_context(session, user_id=seed["admin"].id,
                                               organization_id=unit.id,
                                               membership_id=membership.id)

    now = datetime.now(timezone.utc)
    assert _effective(now.replace(tzinfo=None) - timedelta(seconds=1), now, start=True)
    assert current_write_scope("legacy") == ("legacy", None, None)
    assert await get_visible_organization_ids("legacy") == {"legacy"}
    assert await get_resource_scope(
        "legacy", user_id=seed["admin"].id, legacy_owner_required=True) == ({"legacy"}, True)
    individual_context = type("Context", (), {
        "user_id": seed["admin"].id, "company_id": seed["company"].id,
        "organization_id": seed["person"].id, "membership_id": "member",
        "organization_level": "individual",
        "path": seed["person"].path, "context_version": 1, "is_primary": False,
    })()
    token = set_organization_context(individual_context)
    try:
        assert current_write_scope("legacy") == (
            seed["person"].id, seed["company"].id, "member")
        ids, owner_required = await get_resource_scope("legacy", user_id=seed["admin"].id)
        assert owner_required and seed["person"].id in ids
        with pytest.raises(HTTPException, match="individual scope"):
            require_resource_scope(individual_context, company_id=seed["company"].id,
                                   organization_id=seed["person"].id,
                                   allowed_ids={seed["person"].id}, owner_id="other")
        require_resource_scope(individual_context, company_id=seed["company"].id,
                               organization_id=seed["person"].id,
                               allowed_ids={seed["person"].id},
                               owner_id=seed["admin"].id)
        with pytest.raises(HTTPException, match="organization scope"):
            require_resource_scope(individual_context, company_id="other",
                                   organization_id=seed["person"].id,
                                   allowed_ids={seed["person"].id},
                                   owner_id=seed["admin"].id)
    finally:
        reset_organization_context(token)


@pytest.mark.asyncio
async def test_service_move_and_membership_negative_invariants(init_rag_db):
    from auth import hash_password
    from models.database import async_session
    from models.schemas import User, UserRole
    from organization_service import (
        OrganizationError, create_organization_membership, create_organization_unit,
        disable_membership, move_organization_unit, set_primary_membership,
    )

    seed = await _seed()
    inactive = User(id=str(uuid.uuid4()), username=f"inactive-{uuid.uuid4().hex[:8]}",
                    password_hash=hash_password("Inactive-123"), role=UserRole.viewer,
                    is_active=False)
    async with async_session() as session:
        for kwargs, code in [
            ({"name": "   ", "level": "company"}, "ORG_NAME_REQUIRED"),
            ({"name": "Child Company", "level": "company",
              "parent_id": seed["company"].id}, "ORG_INVALID_PARENT"),
            ({"name": "No Parent", "level": "department"}, "ORG_PARENT_REQUIRED"),
        ]:
            with pytest.raises(OrganizationError) as exc:
                await create_organization_unit(session, **kwargs)
            assert exc.value.code == code
        session.add(inactive)
        await session.commit()
        with pytest.raises(OrganizationError) as exc:
            await create_organization_membership(
                session, user_id=inactive.id, organization_id=seed["company"].id)
        assert exc.value.code == "ORG_USER_NOT_FOUND"
        with pytest.raises(OrganizationError) as exc:
            await create_organization_membership(
                session, user_id=seed["admin"].id, organization_id="missing")
        assert exc.value.code == "ORG_NOT_FOUND"
        with pytest.raises(OrganizationError) as exc:
            await create_organization_membership(
                session, user_id=seed["admin"].id, organization_id=seed["company"].id)
        assert exc.value.code == "ORG_MEMBERSHIP_EXISTS"
        with pytest.raises(OrganizationError) as exc:
            await set_primary_membership(session, "missing")
        assert exc.value.code == "ORG_MEMBERSHIP_NOT_FOUND"
        with pytest.raises(OrganizationError) as exc:
            await disable_membership(session, "missing")
        assert exc.value.code == "ORG_MEMBERSHIP_NOT_FOUND"
        with pytest.raises(OrganizationError) as exc:
            await move_organization_unit(session, unit_id="missing",
                                         new_parent_id=seed["d1"].id, expected_version=1)
        assert exc.value.code == "ORG_NOT_FOUND"
        with pytest.raises(OrganizationError) as exc:
            await move_organization_unit(session, unit_id=seed["group"].id,
                                         new_parent_id=seed["d2"].id, expected_version=999)
        assert exc.value.code == "ORG_VERSION_CONFLICT"
        with pytest.raises(OrganizationError) as exc:
            await move_organization_unit(session, unit_id=seed["group"].id,
                                         new_parent_id=seed["company"].id,
                                         expected_version=seed["group"].context_version)
        assert exc.value.code == "ORG_INVALID_HIERARCHY"

        other_company = await create_organization_unit(
            session, name=f"Other-{uuid.uuid4()}", level="company")
        other_department = await create_organization_unit(
            session, name="Other-D", level="department", parent_id=other_company.id)
        await session.flush()
        with pytest.raises(OrganizationError) as exc:
            await move_organization_unit(session, unit_id=seed["group"].id,
                                         new_parent_id=other_department.id,
                                         expected_version=seed["group"].context_version)
        assert exc.value.code == "ORG_CROSS_COMPANY_MOVE"


@pytest.mark.asyncio
async def test_primary_replacement_required_success_and_expiry(init_rag_db):
    from models.database import async_session
    from organization_service import (
        OrganizationError, create_organization_membership, create_organization_unit,
        disable_membership, set_primary_membership,
    )

    seed = await _seed()
    async with async_session() as session:
        second_company = await create_organization_unit(
            session, name=f"Second-{uuid.uuid4()}", level="company")
        secondary = await create_organization_membership(
            session, user_id=seed["admin"].id, organization_id=second_company.id)
        expired_company = await create_organization_unit(
            session, name=f"Expired-{uuid.uuid4()}", level="company")
        expired = await create_organization_membership(
            session, user_id=seed["target"].id, organization_id=expired_company.id,
            valid_to=datetime.now(timezone.utc) - timedelta(seconds=1))
        solo_department = await create_organization_unit(
            session, name="Solo", level="department", parent_id=second_company.id)
        solo = await create_organization_membership(
            session, user_id=seed["target"].id, organization_id=solo_department.id)
        await session.commit()
        secondary_id, solo_id = secondary.id, solo.id

        # Selecting the already-primary row covers the no-demotion branch.
        old_id, new_id = await set_primary_membership(session, seed["membership"].id)
        assert old_id == new_id == seed["membership"].id
        with pytest.raises(OrganizationError) as exc:
            await set_primary_membership(session, expired.id)
        assert exc.value.code == "ORG_MEMBERSHIP_INACTIVE"
        with pytest.raises(OrganizationError) as exc:
            await disable_membership(session, seed["membership"].id)
        assert exc.value.code == "ORG_PRIMARY_REPLACEMENT_REQUIRED"
        await session.rollback()

    async with async_session() as session:
        disabled = await disable_membership(
            session, seed["membership"].id, secondary_id)
        assert disabled.active is False
        await session.commit()
    async with async_session() as session:
        disabled_solo = await disable_membership(session, solo_id)
        assert disabled_solo.active is False
        await session.commit()
