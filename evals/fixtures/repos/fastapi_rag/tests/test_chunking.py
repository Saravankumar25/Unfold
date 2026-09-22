from app.services.chunking import split_text
from app.services.retriever import reciprocal_rank_fusion


def test_split_text_overlaps() -> None:
    text = "\n\n".join(f"paragraph {i} " * 30 for i in range(10))
    chunks = split_text(text, chunk_size=400, overlap=50)
    assert len(chunks) > 1
    assert all(c.token_count > 0 for c in chunks)


def test_rrf_prefers_items_ranked_by_both() -> None:
    scores = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]])
    assert max(scores, key=scores.get) == 1
