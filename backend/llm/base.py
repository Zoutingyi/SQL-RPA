"""LLM abstraction base class and data types."""

from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    degradation: dict | None = None


class BaseLLM(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    async def chat_stream(
        self, messages: list[ChatMessage], tools: list[dict] | None = None
    ) -> AsyncGenerator[LLMResponse, None]:
        """Yield LLMResponse chunks as they arrive."""
        ...

    @abstractmethod
    async def chat(
        self, messages: list[ChatMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        """Non-streaming chat completion."""
        ...
