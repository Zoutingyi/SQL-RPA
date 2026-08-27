from config import settings
from .base import BaseVectorDB


async def create_vectordb() -> BaseVectorDB:
    # Use Qdrant only when explicitly configured (qdrant_host is set).
    # qdrant_path always has a default so it's not a reliable signal.
    if settings.qdrant_host:
        from .qdrant_impl import QdrantVectorDB
        db = QdrantVectorDB(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            path=settings.qdrant_path or "",
            collection_name=settings.qdrant_collection,
        )
    else:
        from .sqlite_impl import SqliteVectorDB
        db = SqliteVectorDB(settings.database_url)

    await db.ensure_collection(settings.embedding_dim)
    return db
