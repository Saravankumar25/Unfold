"""Local sentence-transformer embeddings."""

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into normalized dense vectors."""
    vectors = _model().encode(texts, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    """Embed a single query."""
    return embed_texts([text])[0]
