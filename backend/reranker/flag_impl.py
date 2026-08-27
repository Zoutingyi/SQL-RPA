"""Reranker implementation using HuggingFace cross-encoder models.

Uses BAAI/bge-reranker-v2-m3 by default — a multilingual cross-encoder that
scores (query, document) pairs for fine-grained relevance ranking.
"""

import logging
from .base import BaseReranker

logger = logging.getLogger("sql_rpa")


class CrossEncoderReranker(BaseReranker):
    """Cross-encoder reranker using HuggingFace transformers.

    Loads the model on first use. Models are cached to HF_HOME or ~/.cache/huggingface.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", use_fp16: bool = True):
        self._model_name = model_name
        self._model = None
        self._tokenizer = None
        self._device = None
        self._use_fp16 = use_fp16
        self._available = False

    def _ensure_loaded(self):
        """Lazy-load the cross-encoder model on first call (blocking)."""
        if self._model is not None:
            return

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self._model_name)

            if self._use_fp16 and self._device == "cuda":
                self._model = self._model.half()
            self._model = self._model.to(self._device)
            self._model.eval()

            self._available = True
            logger.info(f"Reranker loaded: {self._model_name} on {self._device}")
        except Exception as e:
            logger.error(f"Failed to load reranker model {self._model_name}: {e}")
            self._available = False
            raise

    async def ensure_loaded_async(self):
        """Non-blocking version: offloads model loading to a thread."""
        if self._model is not None:
            return
        import asyncio
        await asyncio.to_thread(self._ensure_loaded)

    def is_available(self) -> bool:
        if self._model is not None:
            return self._available
        try:
            self._ensure_loaded()
        except Exception:
            pass
        return self._available

    async def rerank_async(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Async version of rerank. Offloads model loading to thread; inference runs on current thread with no_grad.

        Returns list of (original_index, score) sorted by score descending.
        """
        if not documents:
            return []

        await self.ensure_loaded_async()

        if not self._available or self._model is None:
            return [(i, 0.0) for i in range(len(documents))]

        import torch

        pairs = [[query, doc] for doc in documents]

        with torch.no_grad():
            inputs = self._tokenizer(
                pairs, padding=True, truncation=True,
                max_length=512, return_tensors="pt",
            ).to(self._device)

            scores = self._model(**inputs, return_dict=True).logits.view(-1).float()
            scores = scores.cpu().tolist()

        indexed = [(i, round(s, 6)) for i, s in enumerate(scores)]
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed

    def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Sync version — kept for backward compatibility. Prefer rerank_async in async contexts."""
        if not documents:
            return []

        self._ensure_loaded()

        if not self._available or self._model is None:
            return [(i, 0.0) for i in range(len(documents))]

        import torch

        pairs = [[query, doc] for doc in documents]

        with torch.no_grad():
            inputs = self._tokenizer(
                pairs, padding=True, truncation=True,
                max_length=512, return_tensors="pt",
            ).to(self._device)

            scores = self._model(**inputs, return_dict=True).logits.view(-1).float()
            scores = scores.cpu().tolist()

        indexed = [(i, round(s, 6)) for i, s in enumerate(scores)]
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed
