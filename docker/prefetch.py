"""Image build step: download and load every model/grammar the app needs so containers start offline-capable.

Run inside the image after `uv sync` (see docker/backend.Dockerfile). It exercises the same loaders the app uses
(`Embedder`, `CrossEncoderReranker`, tree-sitter-language-pack), so a broken cache fails the build, not a job.

    python /app/docker/prefetch.py models      # fastembed embedding + cross-encoder (api and worker)
    python /app/docker/prefetch.py grammars    # tree-sitter grammars (worker: analysis)
"""

# ruff: noqa: T201
from __future__ import annotations

import sys

GRAMMARS = [
    "c", "cpp", "csharp", "go", "java", "javascript", "kotlin", "php", "python", "ruby", "rust", "scala",
    "swift", "tsx", "typescript",
]  # fmt: skip


def models() -> None:
    from unfold.core.config import get_settings
    from unfold.indexing.embeddings import Embedder
    from unfold.indexing.reranker import CrossEncoderReranker

    settings = get_settings()
    vectors = Embedder.from_settings(settings)._embed_sync(["warm up the embedding model"], False)
    scores = CrossEncoderReranker.from_settings(settings)._rerank_sync("warm up", ["the reranker model"])
    print(f"models ok: embedding dim={vectors.shape[1]} reranker scores={len(scores)} -> {settings.model_cache_dir}")


def grammars() -> None:
    import tree_sitter_language_pack as tslp

    tslp.prefetch(GRAMMARS)
    missing = sorted(set(GRAMMARS) - set(tslp.downloaded_languages()))
    if missing:
        raise SystemExit(f"grammars missing after prefetch: {missing}")
    for name in GRAMMARS:
        tslp.get_parser(name)  # type: ignore[arg-type]
    print(f"grammars ok: {len(GRAMMARS)} -> {tslp.cache_dir()}")


if __name__ == "__main__":
    targets = sys.argv[1:] or ["models", "grammars"]
    for target in targets:
        {"models": models, "grammars": grammars}[target]()
