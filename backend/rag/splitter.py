from dataclasses import dataclass
from config import settings


@dataclass
class TextChunk:
    chunk_id: str
    text: str
    chunk_index: int


def split_text(text: str, doc_id: str = "", chunk_size: int = 0, overlap: int = 0) -> list[TextChunk]:
    chunk_size = chunk_size or settings.chunk_size
    overlap = overlap or settings.chunk_overlap

    if not text.strip():
        return []

    chunks = []
    start = 0
    idx = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunk_id = f"{doc_id}_chunk_{idx}" if doc_id else f"chunk_{idx}"
            chunks.append(TextChunk(chunk_id=chunk_id, text=chunk_text, chunk_index=idx))
            idx += 1
        start += chunk_size - overlap
        if start >= text_len:
            break

    return chunks
