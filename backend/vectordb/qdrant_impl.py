from qdrant_client import QdrantClient as QdrantSyncClient
from qdrant_client.models import (Distance, VectorParams, PointStruct, Filter,
                                  FieldCondition, MatchAny, MatchValue)
from .base import BaseVectorDB, VectorSearchResult


class QdrantVectorDB(BaseVectorDB):
    def __init__(self, host: str = "", port: int = 6333, path: str = "",
                 collection_name: str = "rag_chunks"):
        self._host = host
        self._port = port
        self._path = path
        self._collection = collection_name
        self._client: QdrantSyncClient | None = None

    def _get_client(self) -> QdrantSyncClient:
        if self._client is None:
            if self._path:
                self._client = QdrantSyncClient(path=self._path)
            else:
                self._client = QdrantSyncClient(host=self._host, port=self._port)
        return self._client

    async def upsert(self, points: list[dict]) -> None:
        from auth import get_tenant_id
        tenant_id = get_tenant_id()
        client = self._get_client()
        qdrant_points = []
        for p in points:
            payload = p.get("payload", {})
            qdrant_points.append(PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload={"tenant_id": tenant_id, "document_id": payload.get("document_id", ""), "text": payload.get("text", "")},
            ))
        client.upsert(collection_name=self._collection, points=qdrant_points)

    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        from auth import get_tenant_id
        from organization_context import get_visible_organization_ids
        scope_ids = await get_visible_organization_ids(get_tenant_id())
        tenant_filter = Filter(must=[FieldCondition(
            key="tenant_id", match=MatchAny(any=list(scope_ids)))])
        client = self._get_client()
        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=self._collection, query=vector, limit=top_k,
                query_filter=tenant_filter,
            )
            results = response.points
        else:  # qdrant-client < 1.10 compatibility
            results = client.search(
                collection_name=self._collection,
                query_vector=vector,
                limit=top_k,
                query_filter=tenant_filter,
            )
        return [
            VectorSearchResult(
                chunk_id=r.id,
                document_id=r.payload.get("document_id", ""),
                text=r.payload.get("text", ""),
                score=r.score,
            )
            for r in results
        ]

    async def delete_by_document(self, document_id: str) -> None:
        from auth import get_tenant_id
        client = self._get_client()
        client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[FieldCondition(key="tenant_id", match=MatchValue(value=get_tenant_id())),
                      FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
        )

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        await self.delete_by_ids(chunk_ids)

    async def delete_by_ids(self, ids: list[str]) -> None:
        client = self._get_client()
        client.delete(collection_name=self._collection, points_selector=ids)

    async def collection_exists(self) -> bool:
        client = self._get_client()
        try:
            client.get_collection(self._collection)
            return True
        except Exception:
            return False

    async def create_collection(self, vector_size: int) -> None:
        client = self._get_client()
        client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    async def ensure_collection(self, vector_size: int) -> None:
        if not await self.collection_exists():
            await self.create_collection(vector_size)
