# Guide quality report: pipeline-v1 vs pipeline-v2 (task 3.9)

Status: measured 2026-10-10 (evening, local), on the commit recorded in `evals/baselines/pipeline-v2/README.md`.
Design under test: `docs/design/investigation-engine.md` (ADR 0017). Everything below is read from the stored runs
in `evals/baselines/pipeline-v1/` and `evals/baselines/pipeline-v2/`; nothing is estimated.

## 1. Result in one paragraph

On the two repositories measured, v2 does not fall more than 0.3 below v1 on any scored dimension, resolves every
citation it makes, and leaves no unsupported project fact standing, in two independent runs. It is better on
coverage, groundedness, depth proxy and specificity. It costs more: 2.5x (fastapi_rag) and 1.2x (requests) the
tokens, and 3.8x and 2.6x the model calls. Readability and judged depth/specificity are not measured: the human
review sheets are generated and unfilled. Two repositories and two runs each are a thin sample; read it as evidence
that the engine works and does not regress, not as a general quality figure.

## 2. Method

- **What ran.** The real worker process (`python -m unfold.worker.run analysis <job>`) via
  `unfold.evaluation.baseline`, against Postgres, Redis, Neo4j, Qdrant, MinIO and the Gemini API. Every model call
  was served by `gemini-3.5-flash-lite` (the only model in each run's `by_model`). No scripted model anywhere.
- **Repositories.** `evals/fixtures/repos/fastapi_rag` (small, own fixture) and `psf/requests` at
  `0e322af87745eff34caffe4df68456ebc20d9068`.
- **Scoring.** `unfold.evaluation.rubric` (`poe eval-guide`): deterministic, no model, on the stored
  `guide.json`, `ir.json`, `chunks.json`. 0-4 per dimension. The scorer shares its citation primitives with the
  verifier (`guide.verify`), so "a resolved citation" means the same thing in both.
- **Gate** (design section 7): no dimension more than 0.3 below the recorded baseline; citation resolve rate >= 0.98;
  zero unsupported statements presented as fact.
- **Replicate.** The same commit was run a second time (`evals/baselines/pipeline-v2/replicate/`).

## 3. Scores

| Dimension | fastapi_rag v1 | v2 | v2 replicate | requests v1 | v2 | v2 replicate |
|---|---|---|---|---|---|---|
| coverage | 3.64 | 3.82 | 3.82 | 2.4 | 3.2 | 4.0 |
| groundedness | 1.25 | 2.67 | 2.44 | 1.33 | 3.48 | 2.78 |
| citation_accuracy | 4.0 | 4.0 | 4.0 | 3.73 | 4.0 | 4.0 |
| depth_proxy | 2.0 | 2.83 | 3.0 | 1.5 | 2.5 | 2.5 |
| specificity | 0.85 | 1.6 | 1.85 | 0.57 | 1.81 | 1.41 |
| redundancy | 4.0 | 3.75 | 3.83 | 4.0 | 3.89 | 3.91 |
| uncertainty_honesty | 0.0 | 4.0 | 4.0 | 4.0 | 4.0 | 4.0 |
| readability, judged depth, judged specificity | not measured | | | not measured | | |

Largest drop against v1 on any run: redundancy on fastapi_rag, -0.25. Raw numbers behind every cell are in each
`scores.json`.

### Cost (one worker attempt each, 0 retries, 0 rate-limited)

| | fastapi_rag v1 | v2 | v2 replicate | requests v1 | v2 | v2 replicate |
|---|---|---|---|---|---|---|
| LLM calls | 5 | 19 | 20 | 5 | 13 | 13 |
| of which plan / write / critique / revise | 1/4*/0/0 | 1/6/6/6 | 1/6/6/7 | 1/4/0/0 | 1/4/4/4 | 1/4/4/4 |
| tokens in | 17130 | 43018 | 45620 | 30512 | 34716 | 37183 |
| tokens out | 4553 | 11140 | 12191 | 3156 | 6530 | 8372 |
| wall seconds | 81.0 | 118.4 | 116.0 | 262.9 | 84.6 | 71.8 |
| cost | not measured | not measured | | not measured | not measured | |

*v1 wrote 4 chapters on fastapi_rag; v2's plan has 6, so the calls are not for the same amount of guide. v1's
requests wall time ran with a warm embedding cache and slower retrieval; seconds are the least comparable row and
were taken on different machine states. Cost is not measured: the repo has no price
table and the keys are free tier.

Every chapter of the primary v2 run went through at least one repair round (the critique flagged something in
all ten), so the
`guide.revise` calls are a fixed tax of about one call per chapter. Whether those rewrites improve the guide or only
change it is not isolated: there is no run without the revise stage to compare against.

## 4. What changed the numbers (and what is not shown)

What is shown by the numbers: groundedness, specificity and depth moved up on both repositories in both runs;
uncertainty honesty went from 0.0 to 4.0 on fastapi_rag because v1 left two unverified facts without a stated gap
and v2 leaves none.

What the numbers do not show: which change caused which gain. Plan, research, writer, verifier and reviser changed
together and there is no ablation. The candidate recorded before verification and repair existed
(`evals/candidates/pipeline-v2-wip/`, tasks 3.4-3.6) scored, under the current scorer, fastapi_rag
3.82/2.67/4.0/2.83/1.51/3.75/3.64 and requests 3.2/3.29/3.81/1.5/2.18/3.63/3.2 (same dimension order as the table).
Against it, the verify-and-repair stage is what removed the one unsupported claim and the citation that did not
resolve on requests (resolve rate 0.95 to 1.0), and what raised honesty from 3.2 to 4.0 there.

### Defects found in that candidate and what fixed them

| Finding | Cause (from the stored guides) | Fix |
|---|---|---|
| requests redundancy -0.39 | Each chapter copied the one-line summary of the chapter it builds on into its opening paragraph (the same 15-word sentence in three chapters); the overview and internals chapters were also handed the same two subsystem targets and read the same files. | The writer is told never to copy a dependency's summary, and is shown the title and objective of every other chapter ("Covered in other chapters"). The skeleton gives a later chapter only targets no earlier chapter owns, and component file picks avoid files other chapters name. Result: requests redundancy 3.63 to 3.89 / 3.91 (candidate re-scored with the current scorer). |
| requests uncertainty honesty -0.80 and one uncited claim | One uncited "36 Python code files" statement stood as a project fact; the stage that graded it only marked it. | The verify-and-repair stage removes or qualifies it (sentence level), and the claim goes with the statement. |
| Unplaced unsupported statements (seen during this work) | A claim restating a sentence in other words fell below the placement threshold measured against a whole block, so nothing could be repaired. | Claims are placed at the sentence, bullet or cell with the best Dice overlap over stemmed content words (>= 0.45). Two regression tests use the wordings that failed. |
| A claim the writer listed but the chapter never makes | An INFERRED fact whose only unverified name (`openai`) appears nowhere in the chapter. | Such a claim is recorded in `verification.unstated` and left out of `claims`; it is counted (`claims_not_stated`), never silent. Both final runs needed it 0 times. |

During development, 3 of 6 runs on earlier states of this code left one INFERRED fact standing (fastapi_rag
twice, requests once); the last two rows above are the fixes. The four runs on the final code (two per
repository) leave none. That is four runs, not a rate.

## 5. The scorer, and one change to it

The task asked whether the scorer penalises a guide for stating gaps. Checked against the stored guides:

- `uncertainty_honesty` (stated gaps over stated gaps plus unverified facts left) does not penalise stating a gap;
  it rewards it. Its weakness is the other direction: one unverified fact and no stated gap scores 0.0, however many
  facts are verified. That is the failure it exists to catch, and it was left as it is.
- Two other dimensions did penalise honesty. A sentence such as "The evidence does not show how X is retried." names
  no repository symbol, so each one lowered **specificity**; and the same gap sentence repeated across chapters
  produced repeated word 5-grams and lowered **redundancy** (the requests candidate's overlapping 5-grams included
  "the evidence does not show"). Both dimensions now leave sentences that state a gap out of their statements (the
  `_GAPS` phrases), `uncertainty_honesty` counts more phrasings as a stated gap (`not established`,
  `does not show/specify/state/describe`, and the verifier's own "Not confirmed in the code:"), and the raw numbers
  report `statements_qualified_or_removed` and `claims_not_stated` next to the score so deletion is visible.
- Tests: `test_stating_a_gap_does_not_cost_specificity_or_redundancy`,
  `test_repeating_an_ordinary_sentence_across_chapters_still_counts_as_redundancy`,
  `test_a_statement_qualified_by_the_verifier_is_a_gap_not_a_fact_left_standing`,
  `test_a_guide_that_says_what_it_does_not_show_is_not_marked_down_for_the_wording`.
- The v1 guides re-score to exactly their recorded numbers under the changed scorer (they state no gaps), so the
  comparison above is like for like. The effect on v2 was small (the pre-repair candidate moved by +0.16 on
  specificity and +0.02 on redundancy on fastapi_rag, +0.13 and +0.02 on requests); the gate result does not
  rest on it.

Risk to name: leaving gap sentences out of the specificity denominator could in principle be exploited by padding
a guide with gap sentences in place of generic ones. The regex only matches statements about evidence, and gaps
remain visible in `gaps_stated` and in the guide text, but a human reviewer should still read for padding.

What these dimensions are, plainly (none of it changes a recorded number):

- **`uncertainty_honesty` and "unverified facts left = 0" test the pipeline's own enforcement, not an independent
  measure.** The verify-and-repair stage qualifies or removes every unsupported project fact by construction, so a
  score of 4.0 and a count of 0 follow from that step having run. They show that the step works; they are not
  evidence that the guide states nothing false.
- **`citation_accuracy` shares primitives with the verifier** (`guide.verify`: `path_exists`, `line_in_bounds`, the
  excerpt check). A citation the verifier cannot resolve is also one the scorer cannot, so the 4.0 is agreement
  between two views of one rule set and not a second opinion.
- **The `_GAPS` exclusion is broad.** Any sentence containing "unknown", "unclear", "does not show/specify/state/
  describe" and the other `_GAPS` phrases is left out of specificity and redundancy, including a sentence that says
  it for another reason and also names something. That is the same padding risk seen from the other side.

## 6. Limitations

- **Sample.** Two repositories (one is the project's own fixture), one model tier, one run each plus one
  replicate. Variance between the two runs is visible (requests coverage 3.2 vs 4.0, groundedness 3.48 vs 2.78).
- **Proxies.** Depth is "flow, error handling, code and state indicators present"; specificity is the share of
  sentences naming a repository symbol; groundedness is the verified share of the claims the writer chose to make.
  None of these is "a developer learns the repository faster". The human review sheets exist to measure that and
  are unfilled (section 7).
- **Groundedness can be moved by claiming less.** The writer chooses its claims and the verifier drops claims that no
  statement makes. Removed and not-stated counts are reported (`statements_qualified_or_removed`,
  `claims_not_stated`; 1 and 0 in the primary fastapi_rag run, 0 and 0 in requests), but the ratio alone is not
  proof of better writing.
- **Critique-triggered rewrites are not isolated** (section 3); after a rewrite the critique is not run again, so a
  rewritten block is judged only by the deterministic checks.
- **No cross-revision reuse and call-graph proof levels** (`docs/HLD.md` section 7): not exercised by this
  measurement. Regeneration is covered by integration tests, not by quality runs; hash reuse is not built (ADR 0017).
- **Seconds** were measured with no control for machine load and include the model-runtime change `60652ed`;
  v1's seconds were taken before it.
- **Not measured:** cost in money, the public GitHub rendering of citations in a browser, PDF/Markdown export of
  qualified statements, behaviour on private or very large repositories, non-Python repositories (the third
  fixture family `terraform_k8s` has no guide run).

## 7. Human review status

`review-sheet.json` exists next to every stored guide (`pipeline-v1/*`, `pipeline-v2/*`, `pipeline-v2/replicate/*`):
generated by the rubric, `reviewer: null`, every `readability`, `judged_depth` and `judged_specificity` still
`null`. The design asks for at least three chapters per repository from a person. **Not done.** Until it is, the
readability and judged-depth columns of the rubric are empty and this report makes no claim about them.

## 8. Reproduce

See `evals/baselines/pipeline-v2/README.md`. Re-scoring stored guides needs no stores and no keys:
`uv run poe eval-guide <guide dirs> --out scores.json`.
