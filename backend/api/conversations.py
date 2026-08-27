import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update, delete, desc

from models.database import async_session
from models.schemas import Conversation, Message
from auth import AuthUser, get_current_user
from organization_context import get_resource_scope

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conv_to_dict(c: Conversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


@router.get("")
async def list_conversations(user: AuthUser = Depends(get_current_user)):
    scope_ids, owner_only = await get_resource_scope(
        user.tenant_id, user_id=user.id, legacy_owner_required=True)
    async with async_session() as session:
        query = select(Conversation).where(Conversation.tenant_id.in_(scope_ids))
        if owner_only:
            query = query.where(Conversation.owner_id == user.id)
        result = await session.execute(query.order_by(desc(Conversation.updated_at)).limit(100))
        convs = result.scalars().all()
        return [_conv_to_dict(c) for c in convs]


@router.post("")
async def create_conversation(request: Request, user: AuthUser = Depends(get_current_user)):
    body = await request.json()
    title = body.get("title", "New Chat") if body else "New Chat"
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        conv = Conversation(id=conv_id, tenant_id=user.tenant_id, owner_id=user.id,
                            title=title, created_at=now, updated_at=now)
        session.add(conv)
        await session.commit()

    return {"id": conv_id, "title": title, "created_at": now.isoformat(), "updated_at": now.isoformat()}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, user: AuthUser = Depends(get_current_user)):
    async with async_session() as session:
        owned = await session.scalar(select(Conversation.id).where(
            Conversation.id == conversation_id, Conversation.owner_id == user.id,
            Conversation.tenant_id == user.tenant_id))
        if not owned:
            raise HTTPException(404, "Conversation not found")
        await session.execute(
            delete(Message).where(Message.conversation_id == conversation_id,
                                  Message.tenant_id == user.tenant_id)
        )
        await session.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await session.commit()
    return {"ok": True}


@router.patch("/{conversation_id}")
async def rename_conversation(conversation_id: str, request: Request,
                              user: AuthUser = Depends(get_current_user)):
    body = await request.json()
    title = body.get("title", "Renamed")

    async with async_session() as session:
        result = await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.owner_id == user.id,
                   Conversation.tenant_id == user.tenant_id)
            .values(title=title, updated_at=datetime.now(timezone.utc))
        )
        await session.commit()
        if result.rowcount != 1:
            raise HTTPException(404, "Conversation not found")

    return {"id": conversation_id, "title": title}


@router.get("/{conversation_id}/messages")
async def get_messages(conversation_id: str, user: AuthUser = Depends(get_current_user)):
    scope_ids, owner_only = await get_resource_scope(
        user.tenant_id, user_id=user.id, legacy_owner_required=True)
    async with async_session() as session:
        conditions = [Conversation.id == conversation_id,
                      Conversation.tenant_id.in_(scope_ids)]
        if owner_only:
            conditions.append(Conversation.owner_id == user.id)
        owned = await session.scalar(select(Conversation.id).where(*conditions))
        if not owned:
            raise HTTPException(404, "Conversation not found")
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id,
                   Message.tenant_id.in_(scope_ids))
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_call_id": m.tool_call_id,
                "tool_args": m.tool_args,
                "sources": m.sources,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]
