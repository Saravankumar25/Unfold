"""Split document text into overlapping chunks."""

from dataclasses import dataclass


@dataclass
class TextChunk:
    position: int
    text: str
    token_count: int


def split_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[TextChunk]:
    """Split text on paragraph boundaries into chunks of roughly `chunk_size` characters with overlap."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[TextChunk] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(buffer) + len(paragraph) > chunk_size and buffer:
            chunks.append(_make_chunk(len(chunks), buffer))
            buffer = buffer[-overlap:]
        buffer = f"{buffer}\n\n{paragraph}" if buffer else paragraph
    if buffer:
        chunks.append(_make_chunk(len(chunks), buffer))
    return chunks


def _make_chunk(position: int, text: str) -> TextChunk:
    return TextChunk(position=position, text=text, token_count=len(text.split()))
