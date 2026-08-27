"""HTTP concurrency benchmark. Run against a disposable environment only."""

import argparse
import asyncio
import json
import os
import platform
import statistics
import time
import uuid
from pathlib import Path

import httpx


SCENARIOS = {
    "health": ("GET", "/api/health", None),
    "db-read": ("GET", "/api/db_operations/tables", None),
    "review-submit": ("POST", "/api/db_operations/submit-review", {
        "sql": "UPDATE benchmark_items SET value = value WHERE id = -1",
        "reason": "disposable benchmark request",
    }),
}


async def run(base_url: str, concurrency: int, requests: int, method: str = "GET",
              path: str = "/api/health", payload: dict | None = None,
              token: str = "", max_p95_ms: float = 0, max_error_rate: float = 0.01,
              *, warmup: int = 0, output_json: str = "", organization_id: str = "",
              membership_id: str = "", workers: int = 1, dataset_label: str = ""):
    if concurrency < 1 or requests < 1 or warmup < 0:
        raise ValueError("concurrency/requests must be positive and warmup cannot be negative")
    semaphore = asyncio.Semaphore(concurrency)
    latencies, failures = [], 0

    headers = {
        **({"Authorization": f"Bearer {token}"} if token else {}),
        **({"X-Organization-ID": organization_id} if organization_id else {}),
        **({"X-Membership-ID": membership_id} if membership_id else {}),
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30, headers=headers) as client:
        async def request_once(record: bool):
            nonlocal failures
            async with semaphore:
                started = time.perf_counter()
                failed = False
                try:
                    response = await client.request(method, path, json=payload, headers={
                        "X-Request-ID": f"benchmark-{uuid.uuid4()}"})
                    failed = response.status_code >= 400
                except httpx.HTTPError:
                    failed = True
                if record:
                    latencies.append((time.perf_counter() - started) * 1000)
                    failures += failed
        if warmup:
            await asyncio.gather(*(request_once(False) for _ in range(warmup)))
        started = time.perf_counter()
        await asyncio.gather(*(request_once(True) for _ in range(requests)))
        elapsed = time.perf_counter() - started
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered)-1, int(len(ordered) * .95))]
    error_rate = failures / requests
    result = {
        "request": {"method": method, "path": path},
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "cpu_count": os.cpu_count(), "workers": workers,
                        "dataset": dataset_label or None},
        "concurrency": concurrency, "warmup": warmup,
        "requests": requests, "failures": failures,
        "throughput_rps": round(requests / elapsed, 2),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2),
            "p50": round(ordered[int(len(ordered) * .50)], 2),
            "p95": round(p95, 2),
            "p99": round(ordered[min(len(ordered)-1, int(len(ordered) * .99))], 2),
        },
        "error_rate": round(error_rate, 6),
        "acceptance": {
            "max_p95_ms": max_p95_ms or None, "max_error_rate": max_error_rate,
            "passed": error_rate <= max_error_rate and (not max_p95_ms or p95 <= max_p95_ms),
        },
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if output_json:
        target = Path(output_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["acceptance"]["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--scenario", choices=[*SCENARIOS, "custom"], default="health")
    parser.add_argument("--method", default="GET")
    parser.add_argument("--path", default="/api/health")
    parser.add_argument("--payload-json", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--max-p95-ms", type=float, default=500)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--organization-id", default="")
    parser.add_argument("--membership-id", default="")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dataset-label", default="")
    args = parser.parse_args()
    method, path, payload = SCENARIOS.get(args.scenario, (args.method, args.path, None))
    if args.payload_json:
        payload = json.loads(args.payload_json)
    raise SystemExit(asyncio.run(run(args.base_url, args.concurrency, args.requests,
        method, path, payload, args.token, args.max_p95_ms, args.max_error_rate,
        warmup=args.warmup, output_json=args.output_json,
        organization_id=args.organization_id, membership_id=args.membership_id,
        workers=args.workers, dataset_label=args.dataset_label)))
