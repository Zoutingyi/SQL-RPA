"""Session extraction — batch-extract user facts from conversation history using LLM.

Triggered after each conversation turn when >= 5 new user messages have
accumulated since last_extracted_at. Runs fire-and-forget (does not block chat).
"""

import json
import re
import logging
from datetime import datetime, timezone

from sqlalchemy import select, func, update as sql_update

from models.database import async_session
from models.schemas import Conversation, Message
from memory.store import MemoryStore
from memory.profile import ProfileManager
from llm.base import ChatMessage
from llm.factory import create_llm

logger = logging.getLogger(__name__)

MIN_NEW_MESSAGES = 5


class SessionExtractor:

    def __init__(
        self,
        store: MemoryStore | None = None,
        profile_manager: ProfileManager | None = None,
    ):
        self.store = store or MemoryStore()
        self.profile = profile_manager or ProfileManager()

    async def should_extract(self, conversation_id: str) -> bool:
        async with async_session() as session:
            conv_stmt = select(Conversation.last_extracted_at).where(
                Conversation.id == conversation_id
            )
            result = await session.execute(conv_stmt)
            last_extracted = result.scalar_one_or_none()

            msg_stmt = select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
            )
            if last_extracted:
                msg_stmt = msg_stmt.where(Message.created_at > last_extracted)

            result = await session.execute(msg_stmt)
            count = result.scalar() or 0
            return count >= MIN_NEW_MESSAGES

    async def extract(self, conversation_id: str) -> list[dict]:
        async with async_session() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc())
            )
            result = await session.execute(stmt)
            messages = result.scalars().all()

        if not messages:
            return []

        conversation_text = "\n".join(
            f"[{m.role.upper()}] {m.content or ''}"
            for m in messages
            if m.role in ("user", "assistant") and m.content
        )

        if len(conversation_text) > 8000:
            conversation_text = conversation_text[-8000:]

        extracted = await self._extract_facts_via_llm(conversation_text)

        if not extracted:
            await self._update_extracted_at(conversation_id)
            return []

        saved_facts = []
        for fact in extracted:
            try:
                memory_id = await self.store.add_memory(
                    content=fact["content"],
                    memory_type=fact.get("type", "fact"),
                    conversation_id=conversation_id,
                )
                saved_facts.append({
                    "id": memory_id,
                    "content": fact["content"],
                    "memory_type": fact.get("type", "fact"),
                })
            except Exception as e:
                logger.warning("Failed to save extracted fact: %s", e)

        await self._update_extracted_at(conversation_id)

        if saved_facts:
            await self._regenerate_profile()

        return saved_facts

    async def _extract_facts_via_llm(self, conversation_text: str) -> list[dict]:
        system_prompt = (
            "你是一个用户信息提取器。从以下对话中提取关于用户的明确信息。\n\n"
            "只提取用户在对话中明确陈述的关于自己的信息。不要推测或假设。\n"
            "返回纯JSON数组（不要markdown代码块标记）：\n"
            '[\n  {"content": "用户说过的关于自己的事实", "type": "fact|preference|decision|identity|role"}\n]\n\n'
            "type说明：\n"
            "- identity: 身份信息（姓名、称呼等）\n"
            "- role: 职业/角色信息\n"
            "- preference: 偏好/喜好\n"
            "- decision: 用户做出的决定\n"
            "- fact: 其他一般事实\n\n"
            "如果没有可提取的信息，返回空数组 []。"
        )

        try:
            llm = create_llm()
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=f"对话内容:\n{conversation_text}"),
            ]
            response = await llm.chat(messages)

            if response.content:
                content = response.content.strip()
                if content.startswith("```"):
                    content = re.sub(r"^```(?:json)?\s*", "", content)
                    content = re.sub(r"\s*```$", "", content)
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    return parsed
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM extraction response: %s", e)
        except Exception as e:
            logger.warning("LLM fact extraction failed: %s", e)

        return []

    async def _update_extracted_at(self, conversation_id: str) -> None:
        async with async_session() as session:
            stmt = (
                sql_update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(last_extracted_at=datetime.now(timezone.utc))
            )
            await session.execute(stmt)
            await session.commit()

    async def _regenerate_profile(self) -> None:
        try:
            profile_data = await self.profile.generate_profile()
            if profile_data and profile_data.get("total_memories", 0) > 0:
                await self.profile.save_profile(profile_data=profile_data, memory_ids=[])
                logger.info("Profile regenerated: %s memories", profile_data.get("total_memories"))
        except Exception as e:
            logger.warning("Failed to regenerate profile: %s", e)
