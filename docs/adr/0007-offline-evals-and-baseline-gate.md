# ADR 0007: Offline evals with a recorded baseline

Status: accepted (2026-10-10) · Phase 3

## Decision
- `python -m unfold.evaluation.offline [--suite S] [--gate]` runs four suites over the three fixture repositories
  with hand-written gold in `evals/gold/*.json`, with no datastore, network or model key:
  - groundedness: the deterministic grader against labelled claims (accuracy, hallucinations graded VERIFIED) and the
    real chat answer path with `ScriptedLLM` (grounded ratio, evidence present, citations valid);
  - retrieval: hit rate at 8 and MRR through the real `HybridRetriever` (lexical + structural; dense/graph/rerank
    absent, which retrieval treats as degradation);
  - graph: recall of gold relationships in the IR and agreement of `graph.communities.detect` with gold
    together/apart file pairs (Neo4j traversal is covered by integration tests, not here);
  - injection: scanner recall and false positives, fence integrity, and prompt invariants of the chat path
    (policy present, question fenced, secrets and fence tags neutralised, secret-shaped output redacted).
- `evals/baseline.json` holds each metric's score and its threshold (`min`, or `max` for rates that must stay low).
  `--gate` (poe `eval-*`, the `scripts/check.py` step) and `tests/unit/test_evaluation_offline.py` fail below it.
  `--write-baseline` regenerates it with 0.05 headroom; the diff is reviewed like code.
- The harness proves it can fail: tests break the fence, redaction, scanner and grader and expect the metrics to drop.
- `unfold.evaluation` is test tooling (scripted model, in-memory chunk and blob stores). An architecture test forbids
  production packages from importing it. There is no `eval-online` task: LLM-judged evals need keys and are not
  part of the gate.

## Known limits
Gold questions are easy (lexical retrieval already hits 100 %); the baseline guards regressions, it does not rank
retrieval quality. The scanner flags "act as" in benign prose (recorded as a false-positive rate of 1/6).
