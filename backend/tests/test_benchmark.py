import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_benchmark_warmup_report_and_organization_headers(tmp_path, monkeypatch):
    from benchmarks import run_benchmark

    seen = []

    async def fake_request(self, method, path, **kwargs):
        seen.append(kwargs["headers"])
        return httpx.Response(200, request=httpx.Request(method, f"http://test{path}"))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    report = tmp_path / "capacity.json"
    code = await run_benchmark.run(
        "http://test", 2, 4, token="token", warmup=2,
        organization_id="org-a", membership_id="member-a",
        output_json=str(report), workers=4, dataset_label="acceptance")

    assert code == 0
    assert len(seen) == 6
    assert all(item["X-Request-ID"].startswith("benchmark-") for item in seen)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["warmup"] == 2
    assert payload["requests"] == 4
    assert payload["environment"]["workers"] == 4
    assert payload["environment"]["dataset"] == "acceptance"


@pytest.mark.asyncio
async def test_benchmark_transport_errors_fail_acceptance(monkeypatch):
    from benchmarks import run_benchmark

    async def fail_request(self, method, path, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "request", fail_request)
    assert await run_benchmark.run("http://offline", 1, 2, warmup=1) == 1
