"""ReAct agent loop with SSE event streaming for the SQL-RPA Agent."""

import json
import logging
import traceback
import uuid
import time as time_module
from collections.abc import AsyncGenerator

from llm.base import ChatMessage, LLMResponse, ToolCall
from llm.factory import create_llm
from agent.context import ContextManager
from agent.classifier import llm_classify
from agent.tools import registry as tool_registry
from config import settings

logger = logging.getLogger("sql_rpa")


def _history_to_messages(
    history: list[dict], max_turns: int = 20
) -> list[ChatMessage]:
    """Convert DB message rows to ChatMessage list for the LLM."""
    messages: list[ChatMessage] = []
    for m in history[-max_turns:]:
        msg = ChatMessage(
            role=m["role"],
            content=m.get("content"),
            tool_call_id=m.get("tool_call_id"),
            name=m.get("tool_name"),
        )
        if m.get("tool_calls"):
            try:
                msg.tool_calls = [
                    ToolCall(**tc) for tc in json.loads(m["tool_calls"])
                ]
            except (json.JSONDecodeError, TypeError):
                pass
        messages.append(msg)
    return messages


def _make_event(event: str, data: dict | list | None = None) -> str:
    """Format an SSE event string."""
    payload = data or {}
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_llm_response(
    llm, messages: list[ChatMessage], tools: list[dict] | None
) -> AsyncGenerator[tuple[str, dict | None], None]:
    """Stream LLM response, yielding ('chunk'|'tool_calls'|'done', data)."""
    content_buf = ""
    tool_calls_received: list[ToolCall] = []

    async for chunk in llm.chat_stream(messages, tools=tools):
        if chunk.degradation:
            yield ("degradation", chunk.degradation)
        if chunk.content:
            content_buf += chunk.content
            yield ("chunk", {"delta": chunk.content})
        if chunk.usage:
            yield ("usage", {"usage": chunk.usage})
        if chunk.tool_calls:
            tool_calls_received.extend(chunk.tool_calls)
        if chunk.finish_reason and not chunk.content and not chunk.tool_calls:
            yield ("done", None)
            return

    if tool_calls_received:
        yield ("tool_calls", {"tool_calls": tool_calls_received})
    elif content_buf:
        yield ("done", None)
    else:
        yield ("done", None)


async def run_agent(
    user_message: str,
    conversation_id: str,
    db_history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """Run the ReAct agent loop and yield SSE event strings.

    Yields SSE-formatted strings like:
        event: answer_chunk\\ndata: {"delta": "..."}\\n\\n
        event: done\\ndata: {}\\n\\n
    """
    ctx = ContextManager()
    llm = create_llm()

    # ── Build message history ──
    history_messages = _history_to_messages(db_history or [])

    # ── Intent classification ──
    intent = await llm_classify(user_message, history_messages, conversation_id)
    yield _make_event("thought", {"delta": f"意图识别: {intent.intent} (置信度: {intent.confidence:.0%})"})

    # ── Build system prompt ──
    tools_schema = tool_registry.get_schemas()
    tools_description = "\n".join(
        f"- **{s['function']['name']}**: {s['function']['description']}"
        for s in tools_schema
    )

    # Load user profile for system prompt
    profile_text = ""
    if settings.memory_enabled:
        try:
            from memory.profile import ProfileManager
            pm = ProfileManager()
            profile_text = await pm.get_profile_text()
        except Exception:
            logger.error(f"Profile load error:\n{traceback.format_exc()}")
            profile_text = ""

    system_prompt = ctx.build_system_prompt(
        intent_hint=intent.hint_text,
        tools_description=tools_description,
        db_type=settings.db_type,
        profile_text=profile_text,
    )

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt)
    ]
    messages.extend(history_messages)
    messages.append(ChatMessage(role="user", content=user_message))

    # ── ReAct Loop ──
    max_iterations = settings.max_loop_iterations
    start_time = time_module.monotonic()
    max_time = settings.max_total_time
    sources = []

    for iteration in range(max_iterations):
        elapsed = time_module.monotonic() - start_time
        if elapsed > max_time:
            yield _make_event("error", {"message": "请求超时，请简化问题后重试。"})
            yield _make_event("done")
            return

        # Trim messages to fit context window
        trimmed, dropped_summary, _ = ctx.trim_messages(messages)
        if dropped_summary:
            yield _make_event("thought", {"delta": f"上下文已压缩 (丢掉早期消息摘要: {dropped_summary})"})

        # Call LLM with streaming
        tool_schemas = tool_registry.get_schemas() if iteration < max_iterations - 1 else None

        assistant_content = ""
        assistant_tool_calls: list[ToolCall] = []
        stream_done = False

        try:
            async for event_type, data in _stream_llm_response(llm, trimmed, tool_schemas):
                if event_type == "chunk" and data:
                    assistant_content += data["delta"]
                    yield _make_event("answer_chunk", data)
                elif event_type == "tool_calls" and data:
                    assistant_tool_calls = data["tool_calls"]
                elif event_type == "usage" and data:
                    yield _make_event("usage", data)
                elif event_type == "degradation" and data:
                    yield _make_event("degradation", data)
                elif event_type == "done":
                    stream_done = True
        except Exception:
            logger.error(f"LLM stream error:\n{traceback.format_exc()}")
            yield _make_event("error", {"message": "处理请求时出现内部错误，请稍后重试。"})
            yield _make_event("done")
            return

        # If LLM returned text and no tool calls → final answer
        if assistant_content and not assistant_tool_calls:
            yield _make_event("done")
            return

        # If LLM returned tool calls → execute them
        if assistant_tool_calls:
            # Add assistant message with tool calls
            assistant_msg = ChatMessage(
                role="assistant",
                content=assistant_content if assistant_content else None,
                tool_calls=assistant_tool_calls,
            )
            messages.append(assistant_msg)

            for tc in assistant_tool_calls:
                yield _make_event("tool_call", {
                    "tool": tc.name,
                    "args": tc.arguments,
                    "call_id": tc.id,
                })

                try:
                    args = json.loads(tc.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("Tool arguments must be a JSON object")
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.error(
                        f"Tool arguments failed JSON parsing for {tc.name}. "
                        f"Refusing to execute."
                    )
                    yield _make_event("tool_result", {
                        "tool": tc.name,
                        "success": False,
                        "result_count": 0,
                    })
                    messages.append(ChatMessage(
                        role="tool",
                        content="Error: invalid tool arguments; execution was refused.",
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))
                    continue

                result = await tool_registry.execute(tc.name, **args)

                result_content = (
                    json.dumps(result.data, ensure_ascii=False)
                    if result.success
                    else "Error: tool execution failed or was refused. Do not retry with the same arguments."
                )
                yield _make_event("tool_result", {
                    "tool": tc.name,
                    "success": result.success,
                    "result_count": result.data.get("row_count", result.data.get("count", 0)) if result.success and isinstance(result.data, dict) else 0,
                })

                messages.append(ChatMessage(
                    role="tool",
                    content=result_content,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

                if tc.name == "search_docs" and result.success and isinstance(result.data, dict):
                    for doc in result.data.get("results", []):
                        sources.append({"document_id": doc.get("document_id", ""), "text": doc.get("text", "")})

                # Capture web search results as sources (with URLs for citation)
                if tc.name == "web_search" and result.success and isinstance(result.data, dict):
                    for wr in result.data.get("results", []):
                        sources.append({
                            "title": wr.get("title", ""),
                            "url": wr.get("href", ""),
                            "text": wr.get("body", ""),
                            "source": "web",
                        })

            if sources:
                yield _make_event("sources", sources)

            continue

        # No tool calls and no content → done
        yield _make_event("done")
        return

    # Max iterations reached
    yield _make_event("thought", {"delta": "达到最大迭代次数，生成最终回答..."})
    # One final LLM call without tools to summarize
    messages.append(ChatMessage(
        role="user",
        content="请基于以上所有工具调用结果，用中文给出最终回答。"
    ))
    trimmed, _, _ = ctx.trim_messages(messages)
    final_content = ""
    async for event_type, data in _stream_llm_response(llm, trimmed, None):
        if event_type == "chunk" and data:
            final_content += data["delta"]
            yield _make_event("answer_chunk", data)
        elif event_type == "usage" and data:
            yield _make_event("usage", data)
        elif event_type == "degradation" and data:
            yield _make_event("degradation", data)
    yield _make_event("done")
