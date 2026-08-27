"""Memory interception — extract user facts from messages before the agent loop.

Uses regex patterns for Chinese and English self-introductions, preferences,
role info, and decisions. Optionally validates extracted facts via LLM.
"""

import json
import re
import logging

from memory.store import MemoryStore
from llm.base import ChatMessage
from llm.factory import create_llm

logger = logging.getLogger(__name__)


class MemoryInterceptor:
    """Scans user messages for extractable facts before the agent loop runs."""

    PATTERNS: list[tuple[str, str]] = [
        (r"(?:我是|我叫|我的名字是|我是做|我是搞)\s*(.+?)(?:[。，,\.!！]|$)", "identity"),
        (r"(?:I am|I'm|My name is|call me)\s+(.+?)(?:[.,!]|$)", "identity"),
        (r"(?:我喜欢|我偏好|我比较喜欢|我特别喜欢|我很喜欢|我不喜欢|我讨厌|我不太喜欢|我比较偏好)\s*(.+?)(?:[。，,\.!！]|$)", "preference"),
        (r"(?:I like|I love|I prefer|I enjoy|I dislike|I hate|I don't like)\s+(.+?)(?:[.,!]|$)", "preference"),
        (r"(?:我的职位是|我的角色是|我的工作是|我是做|我从事|我负责)\s*(.+?)(?:[。，,\.!！]|$)", "role"),
        (r"(?:I work as|My role is|I am a|I'm a|I work in)\s+(.+?)(?:[.,!]|$)", "role"),
        (r"(?:我决定|我选择|我已经决定|我最终选择|我打算)\s*(.+?)(?:[。，,\.!！]|$)", "decision"),
        (r"(?:I decided|I chose|I've decided|I choose|I will go with)\s+(.+?)(?:[.,!]|$)", "decision"),
    ]

    def __init__(self, store: MemoryStore | None = None, llm_confirm: bool = True):
        self.store = store or MemoryStore()
        self.llm_confirm = llm_confirm

    async def intercept(
        self, message: str, conversation_id: str | None = None
    ) -> list[dict]:
        if not message or not message.strip():
            return []

        candidates = self._extract_candidates(message)
        if not candidates:
            return []

        confirmed = candidates
        if self.llm_confirm:
            confirmed = await self._llm_confirm(candidates)

        saved = []
        for fact in confirmed:
            try:
                memory_id = await self.store.add_memory(
                    content=fact["content"],
                    memory_type=fact.get("memory_type", "fact"),
                    conversation_id=conversation_id,
                )
                saved.append({
                    "id": memory_id,
                    "content": fact["content"],
                    "memory_type": fact.get("memory_type", "fact"),
                })
                logger.info("Memory saved: [%s] %s", fact.get("memory_type", "fact"), fact["content"][:80])
            except Exception as e:
                logger.warning("Failed to save memory: %s", e)

        return saved

    def _extract_candidates(self, message: str) -> list[dict]:
        candidates = []
        seen = set()

        for pattern, mem_type in self.PATTERNS:
            for match in re.finditer(pattern, message, re.IGNORECASE):
                content = match.group(1).strip()
                if 2 <= len(content) <= 200 and content not in seen:
                    seen.add(content)
                    candidates.append({"content": content, "memory_type": mem_type})

        return candidates

    async def _llm_confirm(self, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return []

        candidates_text = "\n".join(
            f"{i+1}. [{c['memory_type']}] {c['content']}"
            for i, c in enumerate(candidates)
        )

        system_prompt = (
            "你是一个信息提取验证器。检查以下从用户消息中提取的候选事实。"
            "判断每个事实是否真实描述了用户本人（而非一般性陈述或引用）。"
            "返回JSON数组，仅包含有效的事实：\n"
            '[{"index": 1, "content": "修正后的内容", "type": "identity|preference|role|decision|fact"}]\n'
            "忽略那些不是关于用户本人的事实。"
        )

        try:
            llm = create_llm()
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=f"候选事实:\n{candidates_text}"),
            ]
            response = await llm.chat(messages)

            if response.content:
                json_match = re.search(r"\[.*\]", response.content, re.DOTALL)
                if json_match:
                    validated = json.loads(json_match.group())
                    result = []
                    for item in validated:
                        if item.get("content"):
                            result.append({
                                "content": item["content"],
                                "memory_type": item.get("type", "fact"),
                            })
                    return result
        except Exception as e:
            logger.warning("LLM confirmation failed: %s, using regex results as-is", e)

        return candidates
