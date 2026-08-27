"""Reranker factory: creates and caches the reranker singleton."""

import logging
import threading
from config import settings
from .base import BaseReranker

logger = logging.getLogger("sql_rpa")

_reranker: BaseReranker | None = None
_reranker_ready = False
_lock = threading.Lock()


def create_reranker() -> BaseReranker | None:
    """Return the cached reranker, or create one if not yet initialized."""
    global _reranker, _reranker_ready

    if not settings.rerank_enabled:
        return None

    if _reranker is not None:
        return _reranker

    with _lock:
        if _reranker is not None:
            return _reranker
        from .flag_impl import CrossEncoderReranker
        _reranker = CrossEncoderReranker(
            model_name=settings.rerank_model,
            use_fp16=True,
        )
        _reranker_ready = _reranker.is_available()
        return _reranker


async def preload_reranker_async():
    """Preload reranker model at startup (downloads from HuggingFace if needed). Non-blocking."""
    if not settings.rerank_enabled:
        logger.info("Reranker is disabled via config")
        return
    try:
        reranker = create_reranker()
        if reranker is not None:
            await reranker.ensure_loaded_async()
            if _reranker_ready:
                logger.info("Reranker model preloaded successfully")
            else:
                logger.warning("Reranker model not available — check network and model name")
    except Exception as e:
        logger.warning(f"Reranker preload failed: {e}")


def is_reranker_ready() -> bool:
    """Return True if reranker is enabled and the model is loaded."""
    if not settings.rerank_enabled:
        return False
    if _reranker is None:
        try:
            create_reranker()
        except Exception:
            pass
    return _reranker_ready
