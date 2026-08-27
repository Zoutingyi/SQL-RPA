import json
import math
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from .base import BaseVectorDB, VectorSearchResult


class SqliteVectorDB(BaseVectorDB):
    def __init__(self, database_url: str):
        sync_url = database_url.replace("sqlite+aiosqlite:///", "sqlite:///")
        self.engine = create_async_engine(database_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def upsert(self, points: list[dict]) -> None:
        from auth import get_tenant_id
        tenant_id = get_tenant_id()
        async with self.session_factory() as session:
            for p in points:
                vector_json = json.dumps(p["vector"])
                payload = p.get("payload", {})
                await session.execute(
                    sa_text(
                        "INSERT OR REPLACE INTO chunk_vectors "
                        "(chunk_id, tenant_id, document_id, vector_json, text_content, created_at) "
                        "VALUES (:cid, :tenant_id, :did, :vec, :txt, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "cid": p["id"],
                        "tenant_id": tenant_id,
                        "did": payload.get("document_id", ""),
                        "vec": vector_json,
                        "txt": payload.get("text", ""),
                    },
                )
            await session.commit()

    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        from auth import get_tenant_id
        from organization_context import get_visible_organization_ids
        scope_ids = await get_visible_organization_ids(get_tenant_id())
        async with self.session_factory() as session:
            result = await session.execute(
                sa_text("SELECT chunk_id, document_id, vector_json, text_content FROM chunk_vectors WHERE tenant_id IN :tenant_ids")
                .bindparams(__import__("sqlalchemy").bindparam("tenant_ids", expanding=True)),
                {"tenant_ids": list(scope_ids)},
            )
            rows = result.fetchall()

        scored = []
        for row in rows:
            try:
                stored = json.loads(row[2])
                sim = self._cosine(vector, stored)
                scored.append(VectorSearchResult(
                    chunk_id=row[0],
                    document_id=row[1],
                    text=row[3] or "",
                    score=sim,
                ))
            except (json.JSONDecodeError, TypeError):
                continue

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    async def delete_by_document(self, document_id: str) -> None:
        from auth import get_tenant_id
        async with self.session_factory() as session:
            await session.execute(
                sa_text("DELETE FROM chunk_vectors WHERE document_id = :did AND tenant_id = :tenant_id"),
                {"did": document_id, "tenant_id": get_tenant_id()},
            )
            await session.commit()

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        from auth import get_tenant_id
        async with self.session_factory() as session:
            for cid in chunk_ids:
                await session.execute(
                    sa_text("DELETE FROM chunk_vectors WHERE chunk_id = :cid AND tenant_id = :tenant_id"),
                    {"cid": cid, "tenant_id": get_tenant_id()},
                )
            await session.commit()

    async def delete_by_ids(self, ids: list[str]) -> None:
        await self.delete_by_chunks(ids)

    async def collection_exists(self) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                sa_text("SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_vectors'")
            )
            return result.fetchone() is not None

    async def create_collection(self, vector_size: int) -> None:
        async with self.session_factory() as session:
            await session.execute(sa_text(
                "CREATE TABLE IF NOT EXISTS chunk_vectors ("
                "  chunk_id TEXT PRIMARY KEY,"
                "  tenant_id TEXT NOT NULL DEFAULT 'default',"
                "  document_id TEXT NOT NULL,"
                "  vector_json TEXT NOT NULL,"
                "  text_content TEXT DEFAULT '',"
                "  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ")"
            ))
            await session.execute(sa_text(
                "CREATE INDEX IF NOT EXISTS idx_cv_doc ON chunk_vectors(document_id)"
            ))
            await session.commit()

    async def ensure_collection(self, vector_size: int) -> None:
        if not await self.collection_exists():
            await self.create_collection(vector_size)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
