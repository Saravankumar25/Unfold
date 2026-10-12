# pipeline-v2 on the AWS profile (phase 4, 2026-10-11)

Two runs of the same pipeline as `../pipeline-v2` (adaptive plan, per-chapter research, writer, deterministic
verification, one critique, at most two repair rounds) on `evals/fixtures/repos/fastapi_rag`, with the AWS persistence
profile: DynamoDB and S3 over moto (in process; no AWS call was made), the embedded dense artifact, the in-memory graph
and the counter-store LLM coordination. The model is real (`gemini-3.5-flash-lite`, free tier, through the real
gateway). Produced by `scripts/aws_profile_guide_run.py`; scored by `unfold.evaluation.rubric` (`scores.json`).

| | run1 | run2 | v2 (local stack) | v2 replicate |
|---|---|---|---|---|
| coverage | 4.0 | 4.0 | 3.82 | 3.82 |
| groundedness | 2.92 | 2.42 | 2.67 | 2.44 |
| citation_accuracy | 4.0 | 4.0 | 4.0 | 4.0 |
| depth_proxy | 2.67 | 2.83 | 2.83 | 3.0 |
| specificity | 1.33 | 1.46 | 1.6 | 1.85 |
| redundancy | 3.88 | 3.93 | 3.75 | 3.83 |
| uncertainty_honesty | 4.0 | 4.0 | 4.0 | 4.0 |
| calls / tokens in / out | 19 / 41102 / 11813 | 20 / 42617 / 12295 | 19 / 43018 / 11140 | 20 / 45620 / 12191 |
| wall seconds (1 attempt, 2 provider rate-limit waits each) | 286.9 | 400.4 | 118.4 | 116.0 |

Gate (`docs/design/investigation-engine.md` section 7): resolve rate 1.0 and zero unsupported, uncited or unverified
project facts left in both runs; no dimension is more than 0.3 below `../pipeline-v1`. Against the v2 runs the gap is
larger on specificity (-0.27 and -0.14 against v2, -0.52 and -0.39 against its replicate) and depth proxy (-0.16 to
-0.33); the two v2 runs differ from each other by 0.25 on specificity, and two runs cannot separate a backend effect
from model variance. Read it as "no regression shown", not "none". Wall seconds were taken on a loaded laptop and the
profile has no embedding cache (about a minute of the run is embedding); they are not comparable. Human review sheets are not filled.
