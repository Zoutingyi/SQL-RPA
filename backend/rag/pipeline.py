import hashlib
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import text as sa_text
from models.database import async_session
from models.schemas import DocStatus
from rag.loaders import load_file
from rag.splitter import split_text
from rag.progress import progress_tracker
from embedding.factory import create_embedding
from vectordb.factory import create_vectordb
from textdb.fts5_impl import Fts5TextDB
from config import settings

logger = logging.getLogger("sql_rpa")


class IngestionPipeline:
    def __init__(self):
        self._embedding = None
        self._vectordb = None
        self._textdb = None

    async def _get_embedding(self):
        if self._embedding is None:
            self._embedding = create_embedding()
        return self._embedding

    async def _get_vectordb(self):
        if self._vectordb is None:
            self._vectordb = await create_vectordb()
        return self._vectordb

    async def _get_textdb(self):
        if self._textdb is None:
            self._textdb = Fts5TextDB(settings.database_url)
        return self._textdb

    async def _update_status(self, doc_id: str, status: DocStatus, **kwargs):
        async with async_session() as session:
            set_parts = ["status = :status", "updated_at = :now"]
            params = {"doc_id": doc_id, "status": status.value, "now": datetime.now(timezone.utc)}
            for k, v in kwargs.items():
                set_parts.append(f"{k} = :{k}")
                params[k] = v
            await session.execute(
                sa_text(f"UPDATE documents SET {', '.join(set_parts)} WHERE id = :doc_id"),
                params,
            )
            await session.commit()

    async def ingest(self, file_path: str, doc_id: str) -> bool:
        try:
            await self._update_status(doc_id, DocStatus.parsing)
            await progress_tracker.publish(doc_id, "parsing", 10, "正在解析文档...")

            text = await load_file(file_path)

            await self._update_status(doc_id, DocStatus.chunking)
            await progress_tracker.publish(doc_id, "chunking", 25, "正在切分文本块...")

            chunks = split_text(text, doc_id=doc_id)
            if not chunks:
                await self._update_status(doc_id, DocStatus.failed, error_message="No text content extracted")
                await progress_tracker.publish(doc_id, "failed", 100, "文档无有效文本内容")
                return False

            await self._update_status(doc_id, DocStatus.embedding)
            await progress_tracker.publish(doc_id, "embedding", 40, f"正在向量化 ({len(chunks)} 块)...")

            embedding = await self._get_embedding()
            chunk_texts = [c.text for c in chunks]
            vectors = await embedding.embed(chunk_texts)

            await self._update_status(doc_id, DocStatus.indexing)
            await progress_tracker.publish(doc_id, "indexing", 70, "正在写入索引...")

            vectordb = await self._get_vectordb()
            textdb = await self._get_textdb()

            points = []
            for chunk, vector in zip(chunks, vectors):
                points.append({
                    "id": chunk.chunk_id,
                    "vector": vector,
                    "payload": {"document_id": doc_id, "text": chunk.text},
                })

            await vectordb.upsert(points)

            for chunk in chunks:
                await textdb.insert(chunk.chunk_id, doc_id, chunk.text)

            await self._update_status(
                doc_id, DocStatus.ready,
                chunk_count=len(chunks),
                embedding_dim=len(vectors[0]) if vectors else settings.embedding_dim,
                embedding_model=settings.embedding_model,
            )
            await progress_tracker.publish(doc_id, "ready", 100, f"处理完成 ({len(chunks)} 块)")
            return True

        except Exception as e:
            logger.error(f"Ingestion failed for doc {doc_id}: {e}")
            await self._update_status(doc_id, DocStatus.failed, error_message=str(e)[:500])
            await progress_tracker.publish(doc_id, "failed", 100, f"处理失败: {e}")
            return False

    async def delete(self, doc_id: str) -> None:
        vectordb = await self._get_vectordb()
        textdb = await self._get_textdb()
        await vectordb.delete_by_document(doc_id)
        await textdb.delete_by_document(doc_id)

    async def reprocess(self, doc_id: str, file_path: str) -> bool:
        await self.delete(doc_id)
        return await self.ingest(file_path, doc_id)


pipeline = IngestionPipeline()
