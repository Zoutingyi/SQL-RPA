"""OCR factory: creates and caches the OCR engine singleton."""

import logging
import threading
from config import settings
from .base import BaseOCR

logger = logging.getLogger("sql_rpa")

_ocr: BaseOCR | None = None
_ocr_ready = False
_lock = threading.Lock()


def create_ocr() -> BaseOCR | None:
    """Return the cached OCR engine, or create one if not yet initialized."""
    global _ocr, _ocr_ready

    if not settings.ocr_enabled:
        return None

    if _ocr is not None:
        return _ocr

    with _lock:
        if _ocr is not None:
            return _ocr
        from .tesseract_impl import TesseractOCR
        _ocr = TesseractOCR(language=settings.ocr_language if hasattr(settings, 'ocr_language') else "chi_sim+eng")
        _ocr_ready = _ocr.is_available()
        return _ocr


async def preload_ocr_async():
    """Preload OCR engine at startup. Non-blocking."""
    if not settings.ocr_enabled:
        logger.info("OCR is disabled via config")
        return
    try:
        ocr = create_ocr()
        if ocr is not None:
            await ocr.ensure_loaded_async()
            if _ocr_ready:
                logger.info("OCR engine preloaded successfully")
            else:
                logger.warning("OCR engine created but not available — check tesseract installation")
    except Exception as e:
        logger.warning(f"OCR preload failed: {e}")


def is_ocr_ready() -> bool:
    """Return True if OCR is enabled and the engine is available."""
    if not settings.ocr_enabled:
        return False
    if _ocr is None:
        create_ocr()
    return _ocr_ready
