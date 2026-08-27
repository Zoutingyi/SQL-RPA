from datetime import datetime, timedelta, timezone
import uuid
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
async def test_execution_result_is_persisted(client):
    submitted = await client.post("/api/db_operations/submit-review", json={
        "sql": "UPDATE users SET age = 36 WHERE id = 2", "reason": "p2 result"
    })
    assert submitted.json()["risk_score"] >= 35
    assert "operation:update" in submitted.json()["risk_factors"]
    review_id = submitted.json()["id"]
    approved = await client.post(f"/api/db_operations/review/{review_id}/approve", json={})
    assert approved.status_code == 200
    result = (await client.get(f"/api/db_operations/review/{review_id}/execution-result")).json()
    assert result["before"]["rows"] and result["after"]["rows"]
    assert result["affected_rows"] == 1


@pytest.mark.asyncio
async def test_batch_reject(client):
    ids = []
    for user_id in (3, 4):
        response = await client.post("/api/db_operations/submit-review", json={
            "sql": f"DELETE FROM users WHERE id = {user_id}", "reason": "batch"
        })
        ids.append(response.json()["id"])
    response = await client.post("/api/db_operations/reviews/batch", json={
        "review_ids": ids, "action": "reject", "reviewer_note": "batch reject"
    })
    assert response.status_code == 200
    assert response.json()["succeeded"] == 2


@pytest.mark.asyncio
async def test_notification_preferences_and_endpoint_crud(client, monkeypatch):
    import api.notifications as api
    monkeypatch.setattr(api, "_validate_target", lambda target: None)
    preference = await client.put("/api/notifications/preferences", json={
        "event_type": "review.completed", "channel": "in_app", "enabled": False
    })
    assert preference.status_code == 200
    assert (await client.get("/api/notifications/preferences")).json()["items"]
    created = await client.post("/api/notifications/endpoints", json={
        "kind": "webhook", "target": "https://notify.example.test/hook", "enabled": True
    })
    endpoint_id = created.json()["id"]
    assert any(row["id"] == endpoint_id for row in
               (await client.get("/api/notifications/endpoints")).json()["items"])
    updated = await client.put(f"/api/notifications/endpoints/{endpoint_id}", json={
        "kind": "im", "target": "https://notify.example.test/im", "enabled": False
    })
    assert updated.json()["enabled"] is False
    assert (await client.delete(f"/api/notifications/endpoints/{endpoint_id}")).status_code == 204


@pytest.mark.asyncio
async def test_invoice_and_idempotent_payment(client):
    from models.database import async_session
    from models.schemas import LlmUsageLog
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add(LlmUsageLog(id=str(uuid.uuid4()), user_id="dev-user", model="billing-test",
            total_tokens=100, prompt_tokens=80, completion_tokens=20, cost_usd=1.25,
            created_at=now))
        await session.commit()
    invoice = await client.post("/api/billing/invoices", json={
        "period_start": (now - timedelta(minutes=1)).isoformat(),
        "period_end": (now + timedelta(minutes=1)).isoformat(), "user_id": "dev-user"
    })
    assert invoice.status_code == 201
    invoice_id = invoice.json()["id"]
    detail = (await client.get(f"/api/billing/invoices/{invoice_id}")).json()
    assert detail["total_usd"] >= 1.25 and detail["lines"]
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    first = await client.post(f"/api/billing/invoices/{invoice_id}/payments",
                              headers=headers, json={"provider": "manual"})
    second = await client.post(f"/api/billing/invoices/{invoice_id}/payments",
                               headers=headers, json={"provider": "manual"})
    assert first.status_code == 201 and first.json()["id"] == second.json()["id"]
