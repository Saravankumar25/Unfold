# ADR 0013: Embedded dense index and in-memory graph; Qdrant, Neo4j, Redis and S3 Vectors not adopted

Status: proposed (2026-10-10) · Phase 4 stage B

## Context
Dense vectors are 384-d float32 (3.7 MB for 2 424 chunks) and graph/dense are already optional stages. The graph is
deterministic from the IR and only retrieval reads it. Facts: AuraDB Free pauses after 72 h inactivity, resume
"a few minutes to a few hours", console-only for Free, instance deleted after 30 days paused, 50 000 nodes /
175 000 relationships; qdrant-client local mode is exact brute force, single-process lock, warning above 20 000
points; S3 Vectors is GA in us-east-1 and ap-south-1 at $0.06/GB-month and $2.50 per million queries, 2 KB
filterable metadata, no keyword search. There is **no retrieval-quality baseline on `main`**.

## Decision
- Dense: the worker writes an exact-cosine artifact (`ids`, normalised float32 vectors, filter arrays) to
  `projects/{id}/dense/...npz`; retrieval loads it per process and applies the same filters as
  `DenseIndex.search`. Embeddings stay local fastembed (same model, same vectors).
- Graph: an in-memory implementation of the ten `GraphQueries` methods built from the IR; the Neo4j implementation
  stays as the oracle behind `GRAPH_BACKEND=neo4j` until phase 5.
- Redis is removed: rate limits and LLM budget in DynamoDB (ADR 0016), breaker and key-pool state in process,
  response and embedding caches dropped.
- Gates: dense recall@40 >= 0.95 and top-1 agreement >= 95 % against Qdrant on the fixtures and the 2 424-chunk
  input; graph results set-equal on all ten methods over three fixtures. Failing the graph gate means
  `GRAPH_BACKEND=off` (existing degraded mode), reported, never claimed equivalent.

## Alternatives considered
S3 Vectors (not justified below about 50 k vectors per project), Qdrant on EC2/Fargate/Cloud, embedded Qdrant
(still pulls qdrant-client and fastembed imports), AuraDB Free/Professional (Professional $0.09/GB-hour from 1 GB,
about $65.70 per month), Neo4j on t4g.medium ($24.53/$16.35 per month; fallback with an ADR exception).

## Consequences
Chat processes need the IR, chunks, BM25 and dense artifacts in memory (cache per instance, bounded by
`MAX_CHUNKS_PER_PROJECT`). Query embedding and reranking stay local ONNX (152 MB models in the API image).
