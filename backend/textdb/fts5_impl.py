from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from .base import BaseTextDB, TextSearchResult


class Fts5TextDB(BaseTextDB):
    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def insert(self, chunk_id: str, document_id: str, text: str) -> None:
        from auth import get_tenant_id
        async with self.session_factory() as session:
            tenant_id = get_tenant_id()
            await session.execute(sa_text(
                "DELETE FROM chunks_fts_tenant WHERE chunk_id = :cid AND tenant_id = :tenant_id"
            ), {"cid": chunk_id, "tenant_id": tenant_id})
            await session.execute(
                sa_text(
                    "INSERT INTO chunks_fts_tenant (chunk_id, tenant_id, document_id, content) "
                    "VALUES (:cid, :tenant_id, :did, :content)"
                ),
                {"cid": chunk_id, "tenant_id": tenant_id,
                 "did": document_id, "content": text},
            )
            await session.commit()

    async def search(self, query: str, top_k: int = 10) -> list[TextSearchResult]:
        from auth import get_tenant_id
        from organization_context import get_visible_organization_ids
        scope_ids = await get_visible_organization_ids(get_tenant_id())
        fts_query = self._build_fts_query(query)
        async with self.session_factory() as session:
            result = await session.execute(
                sa_text(
                    "SELECT chunk_id, document_id, content, rank FROM chunks_fts_tenant "
                    "WHERE chunks_fts_tenant MATCH :q AND tenant_id IN :tenant_ids "
                    "ORDER BY rank LIMIT :k"
                ).bindparams(__import__("sqlalchemy").bindparam("tenant_ids", expanding=True)),
                {"q": fts_query, "tenant_ids": list(scope_ids), "k": top_k},
            )
            rows = result.fetchall()

        results = []
        for row in rows:
            rank = abs(row[3]) if row[3] else 0.0
            score = 0.0 if rank == 0 else 1.0 / (1.0 + rank)
            results.append(TextSearchResult(
                chunk_id=row[0],
                document_id=row[1],
                text=row[2] or "",
                score=round(score, 6),
            ))
        return results

    async def delete_by_document(self, document_id: str) -> None:
        from auth import get_tenant_id
        async with self.session_factory() as session:
            await session.execute(
                sa_text("DELETE FROM chunks_fts_tenant WHERE document_id = :did AND tenant_id = :tenant_id"),
                {"did": document_id, "tenant_id": get_tenant_id()},
            )
            await session.commit()

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        from auth import get_tenant_id
        async with self.session_factory() as session:
            for cid in chunk_ids:
                await session.execute(
                    sa_text("DELETE FROM chunks_fts_tenant WHERE chunk_id = :cid AND tenant_id = :tenant_id"),
                    {"cid": cid, "tenant_id": get_tenant_id()},
                )
            await session.commit()

    async def count(self) -> int:
        from auth import get_tenant_id
        async with self.session_factory() as session:
            result = await session.execute(sa_text(
                "SELECT COUNT(*) FROM chunks_fts_tenant WHERE tenant_id = :tenant_id"
            ), {"tenant_id": get_tenant_id()})
            row = result.fetchone()
            return row[0] if row else 0

    @staticmethod
    def _build_fts_query(query: str) -> str:
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
