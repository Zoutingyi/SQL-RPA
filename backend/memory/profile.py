"""User profile management — aggregate memories into structured profiles with dedup."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, func, desc

from models.database import async_session
from models.schemas import UserMemory, UserProfile

logger = logging.getLogger(__name__)


class ProfileManager:
    """Aggregates UserMemory rows into a user profile, with deduplication."""

    TYPE_TO_FIELD: dict[str, str] = {
        "fact": "facts",
        "preference": "preferences",
        "decision": "decisions",
        "identity": "identity_info",
        "role": "role_info",
        "skill": "skills",
    }

    DEDUP_THRESHOLD = 0.7

    def __init__(self, tenant_id: str | None = None, owner_id: str | None = None):
        from auth import get_tenant_id
        from agent.identity import get_actor_id
        self.tenant_id = tenant_id or get_tenant_id()
        self.owner_id = owner_id or get_actor_id()

    async def generate_profile(self) -> dict:
        async with async_session() as session:
            stmt = (
                select(UserMemory)
                .where(UserMemory.deprecated == False)
                .where(UserMemory.tenant_id == self.tenant_id,
                       UserMemory.owner_id == self.owner_id)
                .order_by(UserMemory.updated_at.desc())
            )
            result = await session.execute(stmt)
            memories = result.scalars().all()

        grouped: dict[str, list[str]] = {
            "preferences": [],
            "facts": [],
            "decisions": [],
            "identity_info": [],
            "role_info": [],
            "skills": [],
            "other": [],
        }

        memory_ids = []
        for m in memories:
            content = m.content.strip()
            if not content:
                continue
            memory_ids.append(m.id)
            field = self.TYPE_TO_FIELD.get(m.memory_type, "other")
            grouped.setdefault(field, []).append(content)

        for key in grouped:
            grouped[key] = self._deduplicate(grouped[key], self.DEDUP_THRESHOLD)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_memories": len(memory_ids),
            "preferences": grouped["preferences"],
            "facts": grouped["facts"],
            "decisions": grouped["decisions"],
            "identity_info": grouped["identity_info"],
            "role_info": grouped["role_info"],
            "skills": grouped["skills"],
            "other": grouped["other"],
        }

    async def save_profile(
        self, profile_data: dict, memory_ids: list[str]
    ) -> int:
        async with async_session() as session:
            stmt = select(func.max(UserProfile.version)).where(
                UserProfile.tenant_id == self.tenant_id,
                UserProfile.owner_id == self.owner_id)
            result = await session.execute(stmt)
            max_version = result.scalar() or 0

            profile = UserProfile(
                tenant_id=self.tenant_id, owner_id=self.owner_id,
                profile_data=profile_data,
                memory_ids=memory_ids,
                version=max_version + 1,
            )
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            return profile.id

    async def get_latest_profile(self) -> dict | None:
        async with async_session() as session:
            stmt = (
                select(UserProfile)
                .where(UserProfile.tenant_id == self.tenant_id,
                       UserProfile.owner_id == self.owner_id)
                .order_by(desc(UserProfile.version))
                .limit(1)
            )
            result = await session.execute(stmt)
            profile = result.scalar_one_or_none()
            if profile is None:
                return None
            return {
                "id": profile.id,
                "profile_data": profile.profile_data,
                "memory_ids": profile.memory_ids,
                "version": profile.version,
                "generated_at": profile.generated_at.isoformat() if profile.generated_at else None,
            }

    async def get_profile_text(self) -> str:
        profile = await self.get_latest_profile()
        if not profile or not profile.get("profile_data"):
            data = await self.generate_profile()
        else:
            data = profile["profile_data"]
        lines = []

        label_map = {
            "identity_info": "身份信息",
            "role_info": "角色信息",
            "preferences": "偏好",
            "facts": "已知事实",
            "decisions": "历史决策",
            "skills": "技能",
            "other": "其他信息",
        }

        for key, label in label_map.items():
            items = data.get(key, [])
            if items:
                for item in items:
                    lines.append(f"- [{label}] {item}")

        return "\n".join(lines) if lines else ""

    # ── Private ──

    @staticmethod
    def _jaccard_similarity(text1: str, text2: str) -> float:
        set1 = set(text1.lower().split())
        set2 = set(text2.lower().split())
        if not set1 or not set2:
            return 0.0
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    @classmethod
    def _deduplicate(cls, items: list[str], threshold: float) -> list[str]:
        if len(items) <= 1:
            return items
        kept = []
        for item in items:
            is_dup = False
            for existing in kept:
                if cls._jaccard_similarity(item, existing) >= threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(item)
        return kept
