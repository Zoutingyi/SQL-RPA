"""SQLite-backed memory storage with FTS5 full-text search."""

import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select, delete, func, update, or_, text as sa_text

from models.database import async_session
from models.schemas import UserMemory
from config import settings

logger = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self, tenant_id: str | None = None, owner_id: str | None = None):
        from auth import get_tenant_id
        from agent.identity import get_actor_id
        self.tenant_id = tenant_id or get_tenant_id()
        self.owner_id = owner_id or get_actor_id()

    async def add_memory(
        self,
        content: str,
        memory_type: str = "fact",
        conversation_id: str | None = None,
        embedding_model: str | None = None,
    ) -> str:
        memory_id = str(uuid.uuid4())
        async with async_session() as session:
            active_count = await self._count_active_in(session)
            if active_count >= settings.memory_max_count:
                overflow = active_count - settings.memory_max_count + 1
                await self._deprecate_oldest_in(session, overflow)

            memory = UserMemory(
                id=memory_id,
                tenant_id=self.tenant_id, owner_id=self.owner_id,
                content=content,
                memory_type=memory_type,
                deprecated=False,
                embedding_model=embedding_model,
                conversation_id=conversation_id,
            )
            session.add(memory)
            # Sync to FTS5 index
            await session.execute(
                sa_text("INSERT INTO user_memories_fts (memory_id, content) VALUES (:id, :content)"),
                {"id": memory_id, "content": content},
            )
            await session.commit()
        return memory_id

    async def search_memories(
        self, query: str, top_k: int = 5
    ) -> list[dict]:
        if not query or not query.strip():
            return []

        fts_query = self._build_fts_query(query)
        async with async_session() as session:
            result = await session.execute(
                sa_text(
                    "SELECT m.id, m.content, m.memory_type, m.deprecated, "
                    "m.embedding_model, m.conversation_id, m.created_at, m.updated_at "
                    "FROM user_memories m "
                    "INNER JOIN user_memories_fts fts ON m.id = fts.memory_id "
                    "WHERE fts MATCH :q AND m.deprecated = 0 "
                    "AND m.tenant_id = :tenant_id AND m.owner_id = :owner_id "
                    "ORDER BY rank LIMIT :k"
                ),
                {"q": fts_query, "k": top_k, "tenant_id": self.tenant_id,
                 "owner_id": self.owner_id},
            )
            rows = result.fetchall()
            return [
                {
                    "id": r[0], "content": r[1], "memory_type": r[2],
                    "deprecated": r[3], "embedding_model": r[4],
                    "conversation_id": r[5],
                    "created_at": r[6].isoformat() if r[6] else None,
                    "updated_at": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ]

    async def list_memories(
        self, include_deprecated: bool = False
    ) -> list[dict]:
        async with async_session() as session:
            stmt = select(UserMemory).where(
                UserMemory.tenant_id == self.tenant_id,
                UserMemory.owner_id == self.owner_id,
            ).order_by(UserMemory.updated_at.desc())
            if not include_deprecated:
                stmt = stmt.where(UserMemory.deprecated == False)
            result = await session.execute(stmt)
            memories = result.scalars().all()
            return [self._to_dict(m) for m in memories]

    async def get_memory(self, memory_id: str) -> dict | None:
        async with async_session() as session:
            stmt = select(UserMemory).where(UserMemory.id == memory_id,
                UserMemory.tenant_id == self.tenant_id, UserMemory.owner_id == self.owner_id)
            result = await session.execute(stmt)
            memory = result.scalar_one_or_none()
            return self._to_dict(memory) if memory else None

    async def update_memory(
        self,
        memory_id: str,
        content: str | None = None,
        deprecated: bool | None = None,
    ) -> bool:
        from sqlalchemy import update as sql_update
        async with async_session() as session:
            vals = {"updated_at": datetime.now(timezone.utc)}
            if content is not None:
                vals["content"] = content
            if deprecated is not None:
                vals["deprecated"] = deprecated
            if len(vals) == 1:
                return False
            stmt = (
                sql_update(UserMemory)
                .where(UserMemory.id == memory_id, UserMemory.tenant_id == self.tenant_id,
                       UserMemory.owner_id == self.owner_id)
                .values(**vals)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_memory(self, memory_id: str) -> bool:
        async with async_session() as session:
            stmt = delete(UserMemory).where(UserMemory.id == memory_id,
                UserMemory.tenant_id == self.tenant_id, UserMemory.owner_id == self.owner_id)
            result = await session.execute(stmt)
            await session.execute(
                sa_text("DELETE FROM user_memories_fts WHERE memory_id = :id"),
                {"id": memory_id},
            )
            await session.commit()
            return result.rowcount > 0

    async def clear_all(self) -> int:
        async with async_session() as session:
            ids = list((await session.execute(select(UserMemory.id).where(
                UserMemory.tenant_id == self.tenant_id,
                UserMemory.owner_id == self.owner_id))).scalars())
            stmt = delete(UserMemory).where(UserMemory.id.in_(ids))
            result = await session.execute(stmt)
            for memory_id in ids:
                await session.execute(sa_text(
                    "DELETE FROM user_memories_fts WHERE memory_id = :id"), {"id": memory_id})
            await session.commit()
            return result.rowcount

    async def count_active(self) -> int:
        async with async_session() as session:
            return await self._count_active_in(session)

    @staticmethod
    def _build_fts_query(query: str) -> str:
        # Strip FTS5 special characters that break the query syntax
        import re
        clean = re.sub(r'[*(){}\[\]&|^~]', ' ', query)
        terms = clean.strip().split()
        if not terms:
            return '""'
        escaped = []
        for t in terms:
            t = t.replace('"', '""')
            escaped.append(f'"{t}"')
        return " OR ".join(escaped)

    # ── Private ──

    async def _count_active_in(self, session) -> int:
        stmt = select(func.count()).where(UserMemory.deprecated == False,
            UserMemory.tenant_id == self.tenant_id, UserMemory.owner_id == self.owner_id)
        result = await session.execute(stmt)
        return result.scalar() or 0

    async def _deprecate_oldest_in(self, session, count: int) -> None:
        from sqlalchemy import update as sql_update
        stmt = (
            select(UserMemory.id)
            .where(UserMemory.deprecated == False)
            .where(UserMemory.tenant_id == self.tenant_id, UserMemory.owner_id == self.owner_id)
            .order_by(UserMemory.created_at.asc())
            .limit(count)
        )
        result = await session.execute(stmt)
        ids_to_deprecate = [row[0] for row in result.fetchall()]
        if ids_to_deprecate:
            await session.execute(
                sql_update(UserMemory)
                .where(UserMemory.id.in_(ids_to_deprecate))
                .values(deprecated=True, updated_at=datetime.now(timezone.utc))
            )
            for mid in ids_to_deprecate:
                await session.execute(
                    sa_text("DELETE FROM user_memories_fts WHERE memory_id = :id"),
                    {"id": mid},
                )
            logger.info(f"Deprecated {len(ids_to_deprecate)} oldest memories")

    @staticmethod
    def _to_dict(memory: UserMemory) -> dict:
        return {
            "id": memory.id,
            "content": memory.content,
            "memory_type": memory.memory_type,
            "deprecated": memory.deprecated,
            "embedding_model": memory.embedding_model,
            "conversation_id": memory.conversation_id,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        }
