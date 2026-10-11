# Guide-quality regression suite (phase 5, tasks 9 and 10)

Status: built 2026-10-11 on `phase-5-hardening`. It guards the numbers recorded in `evals/baselines/pipeline-v2/`
(method and caveats: `docs/design/quality-report.md`; gate definition: `docs/design/investigation-engine.md` section 7).
It adds no service, store or dependency: two unit-test files, one small module, one on-demand test, four tiny fixtures.

## 1. What runs where

| Check | Runs in | Needs | Where |
|---|---|---|---|
| Stored v2 guides (2 repos x primary and replicate) rescored; each dimension within 0.3 of `scores.json`, hard gates hold | default `uv run pytest`, hence `scripts/check.py` | nothing (no network, no model, no store) | `tests/unit/test_quality_regression.py` |
| Scorer reproduces the recorded numbers exactly (scorer and stored data have not drifted apart) | same | same | same |
| Mutation check: eight corruptions of a stored guide must each fail the gate | same | same | same |
| Outlines differ by project shape, in specific chapters and targets (CLI, web service, monorepo) | same | same | `tests/unit/test_depth_coverage_fixtures.py` |
| Negative fixtures: unsupported language, dynamic dispatch, empty repository, oversized file | same | same | same |
| Regenerate `fastapi_rag` with the real worker and Gemini, rescore, compare with the baseline | `uv run pytest -m llm tests/integration/test_guide_quality_live.py` | stores up, keys in `.env` | on demand |

The unit tests add about 15 s of test time on a quiet machine (most of the wall time is the import of the package).
They are collected by the default pytest run, so `scripts/check.py` runs them; no change to the gate script or to
`evals/baseline.json` was needed. The same check is available as a command:
`uv run python -m unfold.evaluation.regression [baseline_dir ...]` (exit 1 on any problem).

One verified on-demand run (2026-10-11, local, `gemini-3.5-flash-lite` for every call, 18 calls, 43701 tokens in and
12750 out, 1004 s on a loaded machine): coverage 3.82, groundedness 2.76, citation_accuracy 4.0, depth_proxy 2.83,
specificity 1.51, redundancy 3.81, uncertainty_honesty 4.0 (recorded v2: 3.82, 2.67, 4.0, 2.83, 1.6, 3.75, 4.0); 60
citations, resolve rate 1.0, 0 unsupported, 0 uncited, 0 unverified facts left. Honest account: the first attempt at this
test ran the job to success and then failed an assertion whose message was not captured; the second attempt passed.
Treat a single failure as "rerun and read the scores", not as proof of a regression.

## 2. Thresholds (`unfold.evaluation.regression`)

| Rule | Value | Source |
|---|---|---|
| Each of the seven scored dimensions vs the recorded number | within 0.3 on the 0-4 scale (stored guides: either direction; regenerated guide: a drop only) | design section 7 |
| Citation resolve rate | >= 0.98 | design section 7 |
| Citations made vs the recorded guide | >= 80 percent | new: a guide can resolve 3 of 3 by citing almost nothing |
| Project facts `UNSUPPORTED` (presented as fact) | 0 | design section 7 |
| Project facts with no evidence (`uncited`) | 0 | recorded numbers |
| Unverified project facts left standing (`uncertainty_honesty.unverified_facts_left`) | 0 | recorded numbers |
| Prompt-injection signals in the guide text (`security.injection.scan`) | 0 | new, see gaps |

"Within 0.3 either direction" for stored guides is deliberate: on frozen inputs the scorer is deterministic, so a move
means the scorer or the data was edited, and that should be a reviewed change and not a silent one.

The mutation check corrupts the stored JSON and asserts that the named gate reports it: a fabricated cited path, a line
range shifted past the end of the file, every `[E#]` label deleted from the prose, claim evidence removed, a verified
claim turned `UNSUPPORTED`, an injection sentence appended, all but one chapter dropped, one chapter copied over the
others. Each is run on `fastapi_rag` and `requests`.

## 3. Project-shape and negative fixtures (task 10)

Committed under `evals/fixtures/repos/` (hand-written, parsed only, never executed): `cli_tool`, `multi_module`,
`haskell_only`, `dynamic_dispatch`; `fastapi_rag` is the web service. The empty repository and the oversized file are
generated in `tmp_path` (an empty directory cannot be committed; a 1.2 MB file should not be).

What the tests pin down, using `build_architecture` and `build_skeleton` (the planner skeleton before the model sees it):

| Shape | Kind | Chapters | What is specific |
|---|---|---|---|
| `cli_tool` | `cli` | overview, usage, internals, config-deploy | `usage` targets the `command` entry points only; config targets `requirements.txt` |
| `fastapi_rag` | `web_service` | overview, request-lifecycle, data-persistence, background-jobs, config-deploy, + a component chapter | `request-lifecycle` is `deep`, expects `route` evidence, targets only `GET/POST/...` handlers; Dockerfile, compose and pyproject are config targets; PostgreSQL and Qdrant boundaries |
| `multi_module` | `monorepo` | overview, packages, config-deploy, + a file chapter | the three package manifests are the config targets; no entry points and the model says so |

| Negative case | Structured result asserted |
|---|---|
| Haskell only | no symbols, no entry points, no flows; uncertainty is exactly "unsupported language: haskell (no symbols extracted)" and "no entry points detected"; generic outline whose targets are files that exist; scripted planner does not degrade |
| Heavy dynamic dispatch | `getattr` x2 and `importlib.import_module` recorded as unresolved calls; "dynamic dispatch in host.py" in the uncertainty; `trace_flow` of the dispatching function has one step and lists the call; no `CALLS` edge reaches the plugin functions |
| Empty repository | the inventory is empty (the pipeline raises `EMPTY_REPOSITORY` on exactly this); no chapters; the planner raises a typed `GenerationError` |
| Oversized file | the file is not parsed or chunked, the IR counts it as skipped (`files_total` 2, `files_indexed` 1), no symbols, no flows, no chapter targets it |

Every flow in every shape is also checked to contain only located steps reached by an edge with proof other than
`INFERRED`. The scripted model (`unfold.evaluation.scripted`) drives `plan_chapters`; a full pipeline run needs
Postgres, so it is not part of these tests.

## 4. How to refresh a baseline

1. Do not edit numbers in `scores.json` or the stored guides to make a test pass. A failure is the signal.
2. A deliberate pipeline or scorer change: run the real worker for both repositories into a new directory (commands in
   `evals/baselines/pipeline-v2/README.md`, section Reproduce, with `--out evals/baselines/pipeline-v3/...`), run
   `uv run poe eval-guide` on them, review the diff against v2 with `docs/design/quality-report.md` open, record the
   commit, model and tokens in the new README, then point `regression.BASELINE` at the new directory.
3. Keep the old directory: the replicate directory is what shows how much one run varies.
4. A scorer-only change: the stored guides must re-score to their recorded numbers or the change needs a new scores file
   (the v1 guides re-scored unchanged under the v2 scorer; that is the model to follow).
5. On-demand run: `UNFOLD_LIVE_DB_SUFFIX` and `UNFOLD_LIVE_REDIS_DB` choose the database suffix and Redis DB (default
   `_quality` and 9; the regenerated guide is left in the system temp directory under `unfold-live-quality`); it pins all three Gemini tiers to `gemini-3.5-flash-lite` and caps the job at 40 calls and 250k
   tokens, and fails if any other model served a call.

## 5. Known gaps

- **CI cannot catch a regression in generation.** The stored guides only prove the scorer, the gates and the verifier's
  rules. A prompt or planner change that makes real output worse shows up only in the on-demand run.
- **Variance.** One repository, one run, one-sided 0.3. The recorded replicate moved `requests` coverage by 0.8 and
  groundedness by 0.7, so the live test on `fastapi_rag` (its replicate dropped at most 0.23 on any dimension) can still fail on noise; rerun
  before believing it, and do not widen the tolerance to hide it.
- **The proxies of `quality-report.md` section 5 apply.** `uncertainty_honesty` and "unverified facts left = 0" test the
  pipeline's own qualify-or-remove step; `citation_accuracy` shares primitives with the verifier. Readability and judged
  depth are not measured (review sheets unfilled).
- **0.98 lets one bad citation in 50 through.** The two stored guides have 46 and 27 citations at 1.0, so one fabricated
  citation fails here; a guide with 100 citations would pass with two.
- **The injection check is rule-based.** It trips on a guide that quotes a payload while describing it and misses a
  paraphrased instruction that was obeyed. It guards against the verbatim echo only.
- **Oversized files are not named.** `ArchitectureModel.uncertainty` does not say that a code file was skipped by the
  size cap (`understand/architecture.py` `_uncertainty` reads only tier <= 2 files and `FileIR` has no oversized flag), so
  a repository whose only source file is over the cap reports "no entry points detected" and nothing else. Recorded as a
  strict `xfail` (`test_an_oversized_file_is_named_in_the_uncertainty`): it fails the suite when someone fixes it, which
  is the cue to delete the marker.
- **Monorepo packages are one component.** Packages that import each other fall into one community, so the `packages`
  chapter targets that component plus the root and not one target per package (`multi_module`: components `packages`
  with 10 files and `root`). Grounded, but coarse.
- **A repository with no code still gets "how it runs" and "internals".** A README-only project gets three chapters
  whose only target is the root component (the README); the questions presuppose execution that the structured model
  says does not exist. Measured with an ad-hoc tmp-path repository, not asserted by a test.
- **Three project shapes, four negative cases.** The `library`, `pipeline` and `app` backbones have no fixture here.
