"""Dependency-free process metrics suitable for health/operations dashboards."""

from collections import Counter
from threading import Lock

_lock = Lock()
_counters = Counter()
_latency_ms = Counter()


def observe(kind: str, status: str, elapsed_ms: float) -> None:
    with _lock:
        key = f"{kind}:{status}"
        _counters[key] += 1
        _latency_ms[key] += elapsed_ms


def snapshot() -> dict:
    with _lock:
        return {"items": [{
            "key": key, "count": count,
            "avg_latency_ms": round(_latency_ms[key] / count, 2) if count else 0,
        } for key, count in sorted(_counters.items())]}
