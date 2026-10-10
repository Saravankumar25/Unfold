# pipeline-v2 (task 3.9)

The guide pipeline with the investigation engine (ADR 0017): adaptive plan, per-chapter research, writer from the
brief, deterministic verification, one critique, at most two repair rounds, qualified-or-removed leftovers. Measured
by running the real worker (`python -m unfold.worker.run analysis <job>`) against the real stores and the Gemini
API on 2026-10-10 (late evening, local time). Nothing is scripted. Same two repositories, same model tier and same
scorer as `pipeline-v1`, so the two directories are directly comparable.

| | fastapi_rag | requests |
|---|---|---|
| Source | `evals/fixtures/repos/fastapi_rag` (zip upload) | `https://github.com/psf/requests/tree/0e322af87745eff34caffe4df68456ebc20d9068` (tag v2.32.3) |
| Pipeline commit | `c917911` (`phase-3-features`; includes `60652ed`, the local-model batching and ONNX thread cap, which change speed and not output) | same |
| Model | `gemini-3.5-flash-lite` for every call | same |
| Chapters | 6 | 4 |
| LLM calls (ok) / retries | 19 / 0 | 13 / 0 |
| Tokens in / out | 43018 / 11140 | 34716 / 6530 |
| Wall seconds (one worker attempt) | 118.4 | 84.6 |
| Cost | not measured | not measured |

Per-purpose tokens are in `<repo>/run.json` (`guide.plan`, `guide.section`, `guide.critique`, `guide.revise`).
`replicate/` is a second, independent run of both repositories on the same commit.

## Scores (`scores.json`, 0-4, deterministic)

| Dimension | fastapi_rag v1 | v2 | requests v1 | v2 |
|---|---|---|---|---|
| coverage | 3.64 | 3.82 | 2.4 | 3.2 |
| groundedness | 1.25 | 2.67 | 1.33 | 3.48 |
| citation_accuracy | 4.0 | 4.0 | 3.73 | 4.0 |
| depth_proxy | 2.0 | 2.83 | 1.5 | 2.5 |
| specificity | 0.85 | 1.6 | 0.57 | 1.81 |
| redundancy | 4.0 | 3.75 | 4.0 | 3.89 |
| uncertainty_honesty | 0.0 | 4.0 | 4.0 | 4.0 |

Gate (`docs/design/investigation-engine.md` section 7): no dimension more than 0.3 below v1 (largest drop: redundancy
on fastapi_rag, -0.25); citation resolve rate 1.0 on both (>= 0.98); unsupported 0, uncited 0, unverified project
facts left 0 on both. The replicate holds as well (`replicate/scores.json`).

How to read the numbers (none of this changes a recorded value):

- `uncertainty_honesty` and "unverified project facts left 0" test the pipeline's own qualify-or-remove step, not an
  independent measure: that step removes or qualifies every unsupported fact by construction, so 4.0 and 0 show it ran.
- `citation_accuracy` shares its primitives with the verifier (`guide.verify`), so it agrees with it by design and is
  not a second opinion.
- The scorer leaves any sentence containing `unknown`, `unclear`, `does not show/specify/state/describe` (the
  `_GAPS` phrases) out of specificity and redundancy, whatever else the sentence says.

Readability and judged depth/specificity are human fields: `review-sheet.json` in each directory is generated and
still unfilled. Do not read those dimensions off this table.

## Reproduce

```
cd backend    # stores up: docker compose up -d; keys in the git-ignored .env; DATABASE_URL on a migrated database
uv run python -m unfold.evaluation.baseline fastapi_rag --dir ../evals/fixtures/repos/fastapi_rag --out ../evals/baselines/pipeline-v2/fastapi_rag
uv run python -m unfold.evaluation.baseline requests --github https://github.com/psf/requests/tree/0e322af87745eff34caffe4df68456ebc20d9068 --out ../evals/baselines/pipeline-v2/requests
uv run poe eval-guide ../evals/baselines/pipeline-v2/fastapi_rag ../evals/baselines/pipeline-v2/requests --out ../evals/baselines/pipeline-v2/scores.json
```

Caveats: LLM output varies run to run (see `replicate/`: requests coverage 3.2 vs 4.0 and groundedness 3.48 vs 2.78
between two runs of the same commit). Tokens and seconds are one run each; seconds were taken on a machine state that
differs from v1's (and `pipeline-v1`'s requests number had a warm embedding cache). The scorer differs
from the one that produced `pipeline-v1/scores.json` in one respect (sentences that state a gap are left out of
specificity and redundancy, and more phrasings count as a stated gap); the v1 guides re-score to the same numbers.
