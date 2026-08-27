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
    from db_connector.factory import close_connector
    await close_connector()


def test_model_cost_calculation():
    from llm.usage import calculate_cost
    assert calculate_cost("gpt-4o", 1_000_000, 500_000) == 7.5
    assert calculate_cost("unknown", 1000, 1000) == 0


@pytest.mark.asyncio
async def test_llm_circuit_opens_and_recovers(monkeypatch):
    from config import settings
    from llm.resilience import CircuitBreaker

    monkeypatch.setattr(settings, "llm_circuit_failure_threshold", 2)
    monkeypatch.setattr(settings, "llm_circuit_recovery_seconds", 0)
    circuit = CircuitBreaker()
    assert await circuit.allow() is True
    assert await circuit.failure() is False
    assert await circuit.failure() is True
    # Zero recovery interval allows a half-open retry immediately.
    assert await circuit.allow() is True
    await circuit.success()
    assert circuit.failures == 0


@pytest.mark.asyncio
async def test_versioned_policy_is_applied_to_review(client):
    table = f"policy_table_{uuid.uuid4().hex[:8]}"
    created = await client.post("/api/approval-policies", json={
        "name": "sensitive updates", "tables": [table],
        "operation_types": ["UPDATE"], "required_approvals": 2,
    })
    assert created.status_code == 200
    from approval_policy import evaluate_policy
    result = await evaluate_policy("UPDATE", table, 1, "UPDATE x SET secret=1")
    assert result["required_approvals"] == 2
    assert result["policy"].version == 1
    evaluated = await client.post("/api/approval-policies/evaluate", json={
        "operation_type": "UPDATE", "table": table, "affected_rows": 1,
        "sql": "UPDATE x SET secret=1",
    })
    assert evaluated.status_code == 200
    assert evaluated.json()["matches"]
    toggled = await client.post(f"/api/approval-policies/{created.json()['id']}/toggle")
    assert toggled.json()["enabled"] is False


@pytest.mark.asyncio
async def test_review_transfer_escalate_revoke_and_events(client):
    submit = await client.post("/api/db_operations/submit-review", json={
        "sql": "UPDATE users SET age = 33 WHERE id = 3", "reason": "state machine",
    })
    review_id = submit.json()["id"]
    transferred = await client.post(
        f"/api/db_operations/review/{review_id}/actions/transfer",
        json={"assigned_to": "reviewer-2", "reason": "load balance"},
    )
    assert transferred.status_code == 200
    assert transferred.json()["assigned_to"] == "reviewer-2"
    escalated = await client.post(
        f"/api/db_operations/review/{review_id}/actions/escalate", json={}
    )
    assert escalated.json()["status"] == "escalated"
    revoked = await client.post(
        f"/api/db_operations/review/{review_id}/actions/revoke", json={}
    )
    assert revoked.json()["status"] == "revoked"
    events = await client.get(f"/api/db_operations/review/{review_id}/events")
    names = [item["event_type"] for item in events.json()["items"]]
    assert "review.transferred" in names
    assert "review.escalated" in names
    assert "review.revoked" in names


@pytest.mark.asyncio
async def test_error_contract_contains_code_and_request_id(client):
    response = await client.post(
        "/api/db_operations/preview", headers={"X-Request-ID": "p1-contract"}, json={"sql": ""}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["request_id"] == "p1-contract"


@pytest.mark.asyncio
async def test_metrics_endpoint_reports_http_requests(client):
    await client.get("/api/health")
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert any(item["key"].startswith("http:") for item in response.json()["items"])


@pytest.mark.asyncio
async def test_degradation_is_forwarded_as_stream_event():
    from agent.react_loop import _stream_llm_response
    from llm.base import LLMResponse

    class FakeLLM:
        async def chat_stream(self, messages, tools=None):
            yield LLMResponse(degradation={"type": "model_fallback"})
            yield LLMResponse(content="ok", finish_reason="stop")

    events = [event async for event in _stream_llm_response(FakeLLM(), [], None)]
    assert ("degradation", {"type": "model_fallback"}) in events


@pytest.mark.asyncio
async def test_frontend_telemetry_and_usage_overview(client):
    telemetry = await client.post("/api/telemetry", json={
        "event_type": "performance", "page": "/reviews", "duration_ms": 123,
        "request_id": "frontend-rid",
    })
    assert telemetry.status_code == 200
    assert telemetry.json()["accepted"] is True
    overview = await client.get("/api/usage/overview")
    assert overview.status_code == 200
    data = overview.json()
    assert "total_cost_usd" in data and "by_user" in data and "quotas" in data
