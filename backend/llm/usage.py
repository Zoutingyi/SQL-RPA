"""Persist LLM token usage records."""

import json
import uuid

from config import settings
from models.database import async_session
from models.schemas import LlmUsageLog


DEFAULT_PRICES = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    configured = json.loads(settings.model_prices_json or "{}")
    prices = {**DEFAULT_PRICES, **configured}.get(model, {"input": 0, "output": 0})
    return round(
        prompt_tokens * float(prices.get("input", 0)) / 1_000_000
        + completion_tokens * float(prices.get("output", 0)) / 1_000_000,
        8,
    )


async def save_usage(conversation_id: str | None, usage: dict,
                     user_id: str | None = None, request_id: str | None = None,
                     tenant_id: str | None = None) -> None:
    """Persist a usage dict returned by an LLM provider."""
    if not usage:
        return
    async with async_session() as session:
        if tenant_id is None:
            from auth import get_tenant_id
            tenant_id = get_tenant_id()
        model = usage.get("model") or settings.llm_model
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        session.add(LlmUsageLog(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            user_id=user_id, request_id=request_id, provider=settings.llm_provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage.get("total_tokens") or 0,
            cost_usd=calculate_cost(model, prompt_tokens, completion_tokens),
        ))
        await session.commit()
