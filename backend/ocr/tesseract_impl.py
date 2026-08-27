"""OCR implementation using Tesseract OCR via pytesseract.

Supports: PIL Image, numpy array, and PDF pages (via pymupdf page pixmap).
Falls back gracefully if Tesseract is not installed.
"""

import logging
from .base import BaseOCR

logger = logging.getLogger("sql_rpa")


class TesseractOCR(BaseOCR):
    """OCR engine backed by Tesseract.

    Requires tesseract-ocr to be installed on the system:
      - Windows: https://github.com/UB-Mannheim/tesseract/wiki
      - macOS:   brew install tesseract tesseract-lang
      - Linux:   apt install tesseract-ocr tesseract-ocr-chi-sim
    """

    def __init__(self, language: str = "chi_sim+eng"):
        self._language = language
        self._available = False
        self._error_msg = ""
        self._init_done = False
        self._pytesseract = None

    def _do_init(self):
        """Blocking init — called via asyncio.to_thread to avoid blocking the event loop."""
        try:
            import pytesseract
            self._pytesseract = pytesseract
            version = pytesseract.get_tesseract_version()
            logger.info(f"Tesseract OCR ready: version {version}, languages={self._language}")
            self._available = True
        except Exception as e:
            self._error_msg = str(e)
            logger.warning(f"Tesseract OCR not available: {e}. "
                           "Scanned PDFs and images will not be OCR-processed. "
                           "Install tesseract-ocr to enable this feature.")
        finally:
            self._init_done = True

    async def ensure_loaded_async(self):
        """Non-blocking initialization via thread offload."""
        if self._init_done:
            return
        import asyncio
        await asyncio.to_thread(self._do_init)

    def is_available(self) -> bool:
        if not self._init_done:
            self._do_init()
        return self._available

    def recognize(self, image) -> str:
        """Extract text from a PIL Image or numpy array.

        Returns empty string if OCR is unavailable or no text found.
        """
        if not self._available:
            return ""

        try:
            text = self._pytesseract.image_to_string(image, lang=self._language)
            return text.strip() if text else ""
        except Exception as e:
            logger.error(f"OCR recognition error: {e}")
            return ""
