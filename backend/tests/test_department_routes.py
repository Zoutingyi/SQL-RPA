"""Regression coverage for the registered four-level department API contract."""

import uuid

import pytest


def test_department_router_is_registered_with_required_operations():
    from backend.main import app

    openapi_paths = app.openapi()["paths"]
    routes = {(path, method.upper())
              for path, operations in openapi_paths.items()
              for method in operations}

    required = {
        ("/api/departments/tree", "GET"),
        ("/api/departments/memberships/me", "GET"),
        ("/api/departments/context/switch", "POST"),
        ("/api/departments/{unit_id}", "GET"),
        ("/api/departments/{unit_id}", "PUT"),
        ("/api/departments/{unit_id}/move", "POST"),
        ("/api/departments/{unit_id}/disable", "POST"),
        ("/api/departments/{unit_id}/memberships", "GET"),
        ("/api/departments/{unit_id}/memberships", "POST"),
        ("/api/departments/memberships/{membership_id}", "PUT"),
        ("/api/departments/memberships/{membership_id}/set-primary", "POST"),
        ("/api/departments/memberships/{membership_id}/disable", "POST"),
    }

    assert required <= routes


@pytest.mark.asyncio
async def test_organization_audit_contains_request_and_identity_context(init_rag_db):
    from fastapi import Request
    from sqlalchemy import select

    from api.departments import _audit
    from auth import AuthUser, hash_password
    from models.database import async_session
    from models.schemas import DomainEvent, User, UserRole
    from organization_context import resolve_organization_context, set_organization_context
    from organization_service import create_organization_membership, create_organization_unit

    suffix = uuid.uuid4().hex
    user = User(id=str(uuid.uuid4()), username=f"audit-{suffix[:8]}",
                password_hash=hash_password("Audit-Password-123"),
                role=UserRole.viewer, is_active=True)
    async with async_session() as session:
        session.add(user)
        company = await create_organization_unit(
            session, name=f"Audit-{suffix}", level="company")
        membership = await create_organization_membership(
            session, user_id=user.id, organization_id=company.id, role="admin")
        await session.commit()
        context = await resolve_organization_context(
            session, user_id=user.id, organization_id=company.id,
            membership_id=membership.id)
    set_organization_context(context)
    request = Request({"type": "http", "method": "POST", "path": "/api/departments",
                       "headers": [], "client": ("203.0.113.8", 12345)})
    request.state.request_id = f"req-{suffix}"
    actor = AuthUser(id=user.id, username=user.username, role="admin",
                     tenant_id=company.id, company_id=company.id,
                     organization_id=company.id, membership_id=membership.id)

    await _audit(actor, request, "organization.tested", company.id, {"reason": "regression"})

    async with async_session() as session:
        event = await session.scalar(select(DomainEvent).where(
            DomainEvent.event_type == "organization.tested",
            DomainEvent.aggregate_id == company.id))
    assert event.payload == {
        "reason": "regression", "actor_id": user.id, "company_id": company.id,
        "organization_id": company.id, "membership_id": membership.id,
        "request_id": f"req-{suffix}", "source_ip": "203.0.113.8",
        "result": "success",
    }
