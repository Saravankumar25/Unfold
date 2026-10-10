# pipeline-v1 baseline (task 3.1)

The guide pipeline as it was before the investigation engine (phase 3B): one planner call, one retrieval plus one
writer call per section, deterministic `grade_claims`. Everything here was measured on 2026-10-10 by running the real
worker (`python -m unfold.worker.run analysis <job>`) against the real stores and the Gemini API. Nothing is scripted.

| | fastapi_rag | requests |
|---|---|---|
| Source | `evals/fixtures/repos/fastapi_rag` (zip upload) | `https://github.com/psf/requests/tree/0e322af87745eff34caffe4df68456ebc20d9068` (tag v2.32.3) |
| Pipeline commit | `b122e9de3435f4d98c2b5616f20b95ce8bdfa0bd` (phase-3-features, contains main `2cde98f`) | same |
| Model | `gemini-3.5-flash-lite` for every call (plan and sections) | same |
| Chapters | 4 | 4 |
| LLM calls (ok) / retries | 5 / 1 | 5 / 0 |
| Tokens in / out | 17130 / 4553 | 30512 / 3156 |
| Wall seconds (one worker attempt) | 81.0 | 262.9 |
| Cost | not measured | not measured |

Per-purpose tokens, per-model tokens and job state are in `<repo>/run.json` (from the gateway usage log,
`LLMGateway.usage(job_id)`). Cost is not measured: the repo has no price table and the keys are free tier (billed $0);
pass `--usd-per-mtok-in/--usd-per-mtok-out` to `unfold.evaluation.baseline` to compute one.

## Scores (`scores.json`, 0-4, deterministic)

| Dimension | fastapi_rag | requests |
|---|---|---|
| coverage | 3.64 | 2.4 |
| groundedness | 1.25 | 1.33 |
| citation_accuracy | 4.0 | 3.73 |
| depth_proxy | 2.0 | 1.5 |
| specificity | 0.85 | 0.57 |
| redundancy | 4.0 | 4.0 |
| uncertainty_honesty | 0.0 | 4.0 |

Raw numbers behind each score, plus length (words, claims, code blocks), are in `scores.json`. Readability and
judged depth/specificity are human fields: not measured yet, fill `<repo>/review-sheet.json` (>= 3 chapters per repo).

How to read them:

- groundedness is the share of `project_fact` claims `grade_claims` marked VERIFIED. v1 mostly produces
  STRONGLY_INFERRED (the claim names nothing checkable in the cited chunk), not UNSUPPORTED: unsupported count is 0.
- uncertainty_honesty is 4.0 for requests only because it left no unverified (UNSUPPORTED/INFERRED/UNKNOWN) fact
  standing; neither guide states a gap in prose (`gaps_stated` is 0).
- coverage is relative to what the v1 IR names. The requests IR is thin (no endpoints, services `src` and
  `src/requests`, entry files `certs.py` and `help.py`), so its coverage denominator is small and odd.
- depth_proxy: no chapter has a traced flow or error/edge handling (`flow` and `error_edge` rates 0.0).

## Reproduce

```
cd backend    # at the pipeline commit above; stores up: docker compose up -d; keys in the git-ignored .env
uv run python -m unfold.evaluation.baseline fastapi_rag --dir ../evals/fixtures/repos/fastapi_rag --out ../evals/baselines/pipeline-v1/fastapi_rag
uv run python -m unfold.evaluation.baseline requests --github https://github.com/psf/requests/tree/0e322af87745eff34caffe4df68456ebc20d9068 --out ../evals/baselines/pipeline-v1/requests
uv run poe eval-guide ../evals/baselines/pipeline-v1/fastapi_rag ../evals/baselines/pipeline-v1/requests --out ../evals/baselines/pipeline-v1/scores.json
```

Caveats: LLM output varies run to run, so a re-run will not match digit for digit. The fastapi_rag run saw one of
four Gemini keys rejected (`UNAUTHENTICATED`, key index 3) and one retry; the requests run reported 160 dense-vector
cache hits (warm embedding cache from an earlier run), which shortens its wall time. The stored guide directories
(`guide.json`, `ir.json`, `chunks.json`) are exactly what `poe eval-guide` reads, so v2 can be scored the same way.
