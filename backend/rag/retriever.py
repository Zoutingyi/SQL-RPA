import asyncio
from dataclasses import dataclass

from embedding.factory import create_embedding
from vectordb.factory import create_vectordb
from textdb.fts5_impl import Fts5TextDB
from config import settings


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    text: str
    score: float
    source: str  # "semantic" | "keyword" | "hybrid"


class HybridRetriever:
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

    async def retrieve(self, query: str, top_k: int = 0, document_id: str = "") -> list[RetrievedChunk]:
        top_k = top_k or settings.retrieval_top_k

        embedding = await self._get_embedding()
        vectordb = await self._get_vectordb()
        textdb = await self._get_textdb()

        query_vector = await embedding.embed_query(query)

        semantic_task = vectordb.search(query_vector, top_k * 2)
        keyword_task = textdb.search(query, top_k * 2)

        semantic_results, keyword_results = await asyncio.gather(semantic_task, keyword_task)

        # RRF fusion (k=60)
        rrf_scores: dict[str, float] = {}
        chunk_data: dict[str, tuple[str, str, str]] = {}  # chunk_id -> (doc_id, text, source)

        rrf_k = 60

        for rank, r in enumerate(semantic_results, start=1):
            rrf_scores[r.chunk_id] = 1.0 / (rrf_k + rank)
            chunk_data[r.chunk_id] = (r.document_id, r.text, "semantic")

        for rank, r in enumerate(keyword_results, start=1):
            kw_score = 1.0 / (rrf_k + rank)
            if r.chunk_id in rrf_scores:
                rrf_scores[r.chunk_id] += kw_score
                chunk_data[r.chunk_id] = (r.document_id, r.text, "hybrid")
            else:
                rrf_scores[r.chunk_id] = kw_score
                chunk_data[r.chunk_id] = (r.document_id, r.text, "keyword")

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        # Collect all candidates (up to rerank_top_n) for reranking
        rerank_candidates = sorted_ids[:settings.rerank_top_n] if settings.rerank_enabled else sorted_ids[:top_k]

        # ── Reranker: fine-grained cross-encoder scoring ──
        if settings.rerank_enabled and len(rerank_candidates) > 1:
            try:
                from reranker.factory import create_reranker
                reranker = create_reranker()
                if reranker is not None and reranker.is_available():
                    candidate_texts = [chunk_data[cid][1] for cid, _ in rerank_candidates]
                    reranked = await reranker.rerank_async(query, candidate_texts)
                    # Rebuild sorted_ids using reranker scores
                    rerank_candidates = [(rerank_candidates[i][0], score) for i, score in reranked]
            except Exception:
                import logging
                logging.getLogger("sql_rpa").warning(
                    "Reranker failed, falling back to RRF-only scores", exc_info=True
                )

        sorted_ids = rerank_candidates

        results: list[RetrievedChunk] = []
        for chunk_id, score in sorted_ids:
            if document_id and chunk_data[chunk_id][0] != document_id:
                continue
            doc_id, text, source = chunk_data[chunk_id]
            results.append(RetrievedChunk(
                chunk_id=chunk_id,
                document_id=doc_id,
                text=text,
                score=round(score, 6),
                source=source,
            ))
            if len(results) >= top_k:
                break

        if settings.dedup_enabled:
            results = self._dedup(results)

        return results

    def _dedup(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        seen: set[str] = set()
        unique = []
        for c in chunks:
            key = c.text[:100]
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique


retriever = HybridRetriever()
