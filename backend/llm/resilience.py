"""Small process-local LLM circuit breaker with durable degradation events."""

import asyncio
import time
import uuid

from config import settings
from models.database import async_session
from models.schemas import LlmDegradationEvent


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self):
        self.failures = 0
        self.opened_at = 0.0
        self._lock = asyncio.Lock()

    async def allow(self) -> bool:
        async with self._lock:
            if not self.opened_at:
                return True
            if time.monotonic() - self.opened_at >= settings.llm_circuit_recovery_seconds:
                self.opened_at = 0.0
                self.failures = 0
                return True
            return False

    async def success(self) -> None:
        async with self._lock:
            self.failures = 0
            self.opened_at = 0.0

    async def failure(self) -> bool:
        async with self._lock:
            self.failures += 1
            if self.failures >= settings.llm_circuit_failure_threshold:
                newly_opened = not self.opened_at
                self.opened_at = self.opened_at or time.monotonic()
                return newly_opened
            return False


breaker = CircuitBreaker()


async def record_degradation(event_type: str, error: Exception | None = None) -> None:
    try:
        async with async_session() as session:
            session.add(LlmDegradationEvent(
                id=str(uuid.uuid4()), tenant_id=__import__("auth").get_tenant_id(),
                provider=settings.llm_provider,
                primary_model=settings.llm_model,
                fallback_model=settings.llm_fallback_model or None,
                event_type=event_type,
                error_type=type(error).__name__ if error else None,
            ))
            await session.commit()
    except Exception:
        pass
