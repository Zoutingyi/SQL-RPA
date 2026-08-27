import asyncio
import json
import logging
import traceback
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update

from models.database import async_session
from models.schemas import Conversation, Message
from agent.react_loop import run_agent
from agent.identity import set_actor
from auth import get_current_user

logger = logging.getLogger("sql_rpa")

router = APIRouter(tags=["chat"])


@router.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    user_message = body.get("message", "").strip()
    conv_id = body.get("conversation_id")
    actor = await get_current_user(request)
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())

    if not user_message:
        async def empty_stream():
            yield f"event: error\ndata: {json.dumps({'message': '消息不能为空'})}\n\n"
            yield f"event: done\ndata: {json.dumps({})}\n\n"
        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    # Validate LLM config early so we don't save orphan messages on failure
    from config import settings as app_settings
    if not app_settings.llm_api_key:
        async def key_error_stream():
            msg = "LLM_API_KEY 未设置。请在 backend/.env 中配置有效的 API Key。"
            yield f"event: error\ndata: {json.dumps({'message': msg})}\n\n"
            yield f"event: done\ndata: {json.dumps({})}\n\n"
        return StreamingResponse(key_error_stream(), media_type="text/event-stream")

    from api.usage import reserve_user_quota
    quota_reservation_id = await reserve_user_quota(actor.id, request_id, actor.tenant_id)

    async with async_session() as session:
        # Create conversation if new
        is_new = False
        if not conv_id:
            conv_id = str(uuid.uuid4())
            is_new = True
            title = user_message[:80] + ("..." if len(user_message) > 80 else "")
            session.add(Conversation(id=conv_id, tenant_id=actor.tenant_id,
                                     owner_id=actor.id, title=title))
            await session.commit()
        else:
            owned = await session.scalar(select(Conversation.id).where(
                Conversation.id == conv_id, Conversation.owner_id == actor.id,
                Conversation.tenant_id == actor.tenant_id,
            ))
            if not owned:
                raise HTTPException(status_code=404, detail="Conversation not found")

        # Save user message
        user_msg_id = str(uuid.uuid4())
        session.add(Message(
            id=user_msg_id,
            conversation_id=conv_id,
            tenant_id=actor.tenant_id,
            role="user",
            content=user_message,
        ))
        await session.commit()

        # Load history
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.tenant_id == actor.tenant_id)
            .order_by(Message.created_at.asc())
        )
        db_messages = result.scalars().all()
        history = [
            {
                "role": m.role,
                "content": m.content,
                "tool_call_id": m.tool_call_id,
                "tool_name": m.tool_name,
                "tool_calls": m.tool_args,  # stored JSON of tool_calls
            }
            for m in db_messages
        ]

        # Update conversation timestamp
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        await session.commit()

    usage_logs = []

    async def agent_event_stream():
        assistant_content = ""
        assistant_steps = []
        set_actor(actor.id, actor.username, actor.role)

        # ── Memory interception: extract facts from user message (fire-and-forget) ──
        if app_settings.memory_enabled:
            try:
                from agent.intercept import MemoryInterceptor
                interceptor = MemoryInterceptor()
                asyncio.ensure_future(interceptor.intercept(user_message, conv_id))
            except Exception:
                pass

        try:
            async for sse_event in run_agent(user_message, conv_id, history):
                # Capture content for saving to DB
                for line in sse_event.split("\n"):
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            event_line = sse_event.split("\n")[0]
                            event_type = event_line.replace("event: ", "")
                            if event_type == "answer_chunk":
                                assistant_content += data.get("delta", "")
                            if event_type == "usage":
                                usage_logs.append(data.get("usage", {}))
                            assistant_steps.append({"event": event_type, "data": data})
                        except (json.JSONDecodeError, IndexError):
                            pass

                yield sse_event

        except Exception:
            logger.error(f"Chat stream error:\n{traceback.format_exc()}")
            yield f"event: error\ndata: {json.dumps({'message': '处理请求时出现内部错误，请稍后重试。如果问题持续，请联系管理员。'})}\n\n"
            yield f"event: done\ndata: {json.dumps({})}\n\n"

        # Save assistant message
        async with async_session() as session:
            session.add(Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                tenant_id=actor.tenant_id,
                role="assistant",
                content=assistant_content,
                sources=json.dumps([s for s in assistant_steps if s["event"] == "sources"]),
            ))
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conv_id)
                .values(
                    title=_derive_title(assistant_content, user_message),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        # ── Session extraction: check for new facts (fire-and-forget) ──
        if app_settings.memory_enabled:
            try:
                from agent.session_extract import SessionExtractor
                extractor = SessionExtractor()
                asyncio.ensure_future(extractor.extract(conv_id))
            except Exception:
                pass

    async def event_stream():
        try:
            async for event in agent_event_stream():
                yield event
        finally:
            from llm.usage import save_usage
            records = usage_logs or [{
                "model": app_settings.llm_model, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0,
            }]
            for usage in records:
                try:
                    await save_usage(conv_id, usage, actor.id, request_id, actor.tenant_id)
                except Exception:
                    logger.error(f"Failed to persist LLM usage during stream finalization:\n{traceback.format_exc()}")
            from api.usage import settle_quota_reservation
            await settle_quota_reservation(quota_reservation_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Conversation-Id": conv_id},
    )


def _derive_title(assistant_content: str, user_message: str) -> str:
    """Derive a short title from the conversation."""
    candidate = assistant_content or user_message
    first_line = candidate.split("\n")[0].strip()
    return first_line[:80] + ("..." if len(first_line) > 80 else "")
