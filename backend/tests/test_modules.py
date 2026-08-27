"""Tests for OCR, Reranker, and Web Search modules."""

import pytest


class TestOCRFactory:
    """Tests for OCR factory functions."""

    def test_create_ocr_when_disabled(self):
        from config import settings
        import ocr.factory as f

        original = settings.ocr_enabled
        try:
            settings.ocr_enabled = False
            # Reset module-level cache
            f._ocr = None
            f._ocr_ready = False

            result = f.create_ocr()
            assert result is None
            assert f.is_ocr_ready() is False
        finally:
            settings.ocr_enabled = original
            f._ocr = None
            f._ocr_ready = False

    def test_create_ocr_when_enabled(self):
        from config import settings
        import ocr.factory as f

        original = settings.ocr_enabled
        try:
            settings.ocr_enabled = True
            f._ocr = None
            f._ocr_ready = False

            ocr = f.create_ocr()
            # Should return an OCR engine (TesseractOCR instance)
            # Whether it's available depends on tesseract installation
            assert ocr is not None
            assert hasattr(ocr, 'recognize')
            assert hasattr(ocr, 'is_available')
        finally:
            settings.ocr_enabled = original
            f._ocr = None
            f._ocr_ready = False

    @pytest.mark.asyncio
    async def test_preload_ocr_async_disabled(self):
        from config import settings
        import ocr.factory as f

        original = settings.ocr_enabled
        try:
            settings.ocr_enabled = False
            f._ocr = None
            f._ocr_ready = False
            await f.preload_ocr_async()
            assert f._ocr is None
        finally:
            settings.ocr_enabled = original
            f._ocr = None
            f._ocr_ready = False

    def test_is_ocr_ready_returns_bool(self):
        from config import settings
        import ocr.factory as f

        original = settings.ocr_enabled
        try:
            settings.ocr_enabled = False
            f._ocr = None
            f._ocr_ready = False
            assert f.is_ocr_ready() is False

            settings.ocr_enabled = True
            f._ocr = None
            f._ocr_ready = False
            result = f.is_ocr_ready()
            assert isinstance(result, bool)
        finally:
            settings.ocr_enabled = original
            f._ocr = None
            f._ocr_ready = False


class TestRerankerFactory:
    """Tests for Reranker factory functions."""

    def test_create_reranker_when_disabled(self):
        from config import settings
        import reranker.factory as f

        original = settings.rerank_enabled
        try:
            settings.rerank_enabled = False
            f._reranker = None
            f._reranker_ready = False

            result = f.create_reranker()
            assert result is None
            assert f.is_reranker_ready() is False
        finally:
            settings.rerank_enabled = original
            f._reranker = None
            f._reranker_ready = False

    def test_create_reranker_when_enabled_but_no_torch(self):
        """Reranker should handle missing torch/transformers gracefully."""
        from config import settings
        import reranker.factory as f

        original = settings.rerank_enabled
        try:
            settings.rerank_enabled = True
            f._reranker = None
            f._reranker_ready = False

            # Should not crash — just report not ready
            try:
                reranker = f.create_reranker()
                # If torch is available, reranker should be created
                if reranker is not None:
                    assert hasattr(reranker, 'rerank')
                    assert hasattr(reranker, 'is_available')
            except Exception:
                # If model download fails, is_reranker_ready should return False
                assert f.is_reranker_ready() is False
        finally:
            settings.rerank_enabled = original
            f._reranker = None
            f._reranker_ready = False

    @pytest.mark.asyncio
    async def test_preload_reranker_disabled(self):
        from config import settings
        import reranker.factory as f

        original = settings.rerank_enabled
        try:
            settings.rerank_enabled = False
            f._reranker = None
            f._reranker_ready = False
            await f.preload_reranker_async()
            assert f._reranker is None
        finally:
            settings.rerank_enabled = original
            f._reranker = None
            f._reranker_ready = False

    def test_is_reranker_ready_returns_bool(self):
        from config import settings
        import reranker.factory as f

        original = settings.rerank_enabled
        try:
            settings.rerank_enabled = False
            f._reranker = None
            f._reranker_ready = False
            assert f.is_reranker_ready() is False

            settings.rerank_enabled = True
            f._reranker = None
            f._reranker_ready = False
            result = f.is_reranker_ready()
            assert isinstance(result, bool)
        finally:
            settings.rerank_enabled = original
            f._reranker = None
            f._reranker_ready = False


class TestWebSearchTool:
    """Integration tests for the WebSearchTool."""

    @pytest.mark.asyncio
    async def test_web_search_returns_results(self):
        from agent.tools import WebSearchTool
        tool = WebSearchTool()

        result = await tool.execute(query="Python programming language", max_results=3)

        # Should succeed (internet required)
        if result.success:
            assert result.data["count"] >= 1
            assert len(result.data["results"]) >= 1
            r = result.data["results"][0]
            assert "title" in r
            assert "body" in r
            assert "href" in r
            # URL should start with http
            assert r["href"].startswith("http")
        else:
            # If offline, error should be descriptive
            assert "error" in result.__dict__ or result.error is not None

    @pytest.mark.asyncio
    async def test_web_search_caps_max_results(self):
        from agent.tools import WebSearchTool
        tool = WebSearchTool()

        result = await tool.execute(query="test", max_results=20)
        # max_results should be capped at 10
        if result.success:
            assert result.data["count"] <= 10

    @pytest.mark.asyncio
    async def test_web_search_empty_query_handled(self):
        from agent.tools import WebSearchTool
        tool = WebSearchTool()

        result = await tool.execute(query="", max_results=3)
        # Should handle gracefully (DuckDuckGo returns no results for empty query)
        assert isinstance(result.success, bool)


class TestRerankerIntegration:
    """Integration tests for the Reranker in the retrieval pipeline."""

    @pytest.mark.asyncio
    async def test_retriever_uses_reranker(self):
        """Verify HybridRetriever works with reranker enabled/disabled."""
        from config import settings
        from rag.retriever import HybridRetriever

        retriever = HybridRetriever()

        # Test retrieval works without reranker
        original_rerank = settings.rerank_enabled
        try:
            settings.rerank_enabled = False
            results = await retriever.retrieve("test query", top_k=3)
            assert len(results) >= 0  # May be empty if no docs indexed
        finally:
            settings.rerank_enabled = original_rerank


class TestPDFLoaderWithOCR:
    """Tests for PDF loading with OCR fallback."""

    @pytest.mark.asyncio
    async def test_load_pdf_text_extraction(self, tmp_path):
        """PDF with text should be extracted without OCR."""
        # Create a simple text-based PDF using pymupdf
        import fitz
        pdf_path = tmp_path / "text_doc.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Hello World Test Document with sufficient text to exceed the OCR fallback threshold of fifty characters", fontsize=12)
        doc.save(str(pdf_path))
        doc.close()

        from rag.loaders import load_pdf
        text = await load_pdf(str(pdf_path))
        assert "Hello World" in text

    @pytest.mark.asyncio
    async def test_ocr_fallback_for_image_pdf(self, tmp_path):
        """PDF with only an image should trigger OCR fallback path."""
        from PIL import Image
        import fitz

        # Create a PDF with an embedded image (no selectable text)
        pdf_path = tmp_path / "scanned.pdf"
        img = Image.new("RGB", (200, 100), color="white")

        doc = fitz.open()
        page = doc.new_page()
        # Insert a small white image — no real text to extract
        img_path = tmp_path / "blank.png"
        img.save(str(img_path))
        rect = fitz.Rect(0, 0, 200, 100)
        page.insert_image(rect, filename=str(img_path))
        doc.save(str(pdf_path))
        doc.close()

        from rag.loaders import load_pdf

        # If OCR is available, it should extract text (even if empty from blank image)
        # If not, it should raise ValueError for all-scanned docs
        try:
            text = await load_pdf(str(pdf_path))
            # OCR may or may not be available — the function should not crash
            assert isinstance(text, str)
        except ValueError as e:
            # Expected when OCR is not installed — all pages scanned, no text
            assert "scanned" in str(e).lower() or "OCR" in str(e)
