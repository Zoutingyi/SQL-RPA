"""OpenAI-compatible LLM implementation with streaming support."""

import asyncio
import json
import random
import time
from collections.abc import AsyncGenerator
import openai
from openai import AsyncOpenAI
from config import settings
from .base import BaseLLM, ChatMessage, LLMResponse, ToolCall
from .resilience import CircuitOpenError, breaker, record_degradation


class OpenAILLM(BaseLLM):
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        stream_usage: bool = True,
    ):
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=base_url or settings.llm_base_url,
            max_retries=0,
        )
        self.model = model or settings.llm_model
        self.timeout = settings.llm_timeout_seconds
        self.max_retries = max(0, settings.llm_max_retries)
        self.stream_usage = stream_usage
        self._pending_degradation: dict | None = None

    def _should_retry(self, exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.RateLimitError,
                openai.InternalServerError,
            ),
        )

    async def _create_completion(self, payload: dict):
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                started = time.monotonic()
                result = await self.client.chat.completions.create(
                    timeout=self.timeout,
                    **payload,
                )
                from observability import observe
                observe("llm", "ok", (time.monotonic() - started) * 1000)
                return result
            except Exception as exc:
                from observability import observe
                observe("llm", "error", (time.monotonic() - started) * 1000)
                last_exc = exc
                if not self._should_retry(exc) or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 4) + random.uniform(0, 0.25))
        raise last_exc if last_exc else RuntimeError("LLM request failed")

    async def _create_completion_with_fallback(self, payload: dict):
        primary_error: Exception | None = None
        try:
            if not await breaker.allow():
                raise CircuitOpenError("LLM circuit is open")
            result = await self._create_completion(payload)
            await breaker.success()
            return result
        except Exception as exc:
            primary_error = exc
            if not isinstance(exc, CircuitOpenError) and await breaker.failure():
                await record_degradation("circuit_opened", exc)
            fallback = settings.llm_fallback_model
            if fallback and payload.get("model") != fallback:
                fallback_payload = {**payload, "model": fallback}
                self._pending_degradation = {
                    "type": "model_fallback", "primary_model": payload.get("model"),
                    "fallback_model": fallback, "reason": type(exc).__name__,
                }
                await record_degradation("fallback_started", exc)
                try:
                    result = await self._create_completion(fallback_payload)
                    await record_degradation("fallback_succeeded")
                    return result
                except Exception as fallback_exc:
                    await record_degradation("fallback_failed", fallback_exc)
                    raise
            await record_degradation("request_failed", exc)
            raise primary_error

    async def chat(
        self, messages: list[ChatMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": self._format_messages(messages),
        }
        if tools:
            payload["tools"] = tools

        response = await self._create_completion_with_fallback(payload)
        choice = response.choices[0]
        msg = choice.message

        tc = None
        if msg.tool_calls:
            tc = [
                ToolCall(id=t.id, name=t.function.name, arguments=t.function.arguments)
                for t in msg.tool_calls
            ]

        return LLMResponse(
            content=msg.content,
            tool_calls=tc,
            finish_reason=choice.finish_reason,
            usage={
                **response.usage.model_dump(),
                "model": getattr(response, "model", self.model),
            } if response.usage else None,
        )

    async def chat_stream(
        self, messages: list[ChatMessage], tools: list[dict] | None = None
    ) -> AsyncGenerator[LLMResponse, None]:
        payload = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "stream": True,
        }
        if self.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools

        stream = await self._create_completion_with_fallback(payload)

        if self._pending_degradation:
            event = self._pending_degradation
            self._pending_degradation = None
            yield LLMResponse(degradation=event)

        tool_call_buffer: dict[int, dict] = {}

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            content = delta.content or None
            tool_calls = None

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_call_buffer[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_buffer[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_call_buffer[idx]["arguments"] += tc.function.arguments

            finish = chunk.choices[0].finish_reason if chunk.choices else None
            usage = None
            if getattr(chunk, "usage", None):
                usage = {
                    **chunk.usage.model_dump(),
                    "model": getattr(chunk, "model", self.model),
                }

            if finish == "tool_calls" and tool_call_buffer:
                tool_calls = [
                    ToolCall(id=b["id"], name=b["name"], arguments=b["arguments"])
                    for b in tool_call_buffer.values()
                ]
                tool_call_buffer = {}

            yield LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish,
                usage=usage,
            )

    def _format_messages(self, messages: list[ChatMessage]) -> list[dict]:
        formatted = []
        for m in messages:
            msg: dict = {"role": m.role}
            if m.content is not None:
                msg["content"] = m.content
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.name:
                msg["name"] = m.name
            formatted.append(msg)
        return formatted
