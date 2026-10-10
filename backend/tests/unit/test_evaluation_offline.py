"""The offline evals meet the recorded baseline, and they can actually fail (they are not vacuous)."""

from __future__ import annotations

import gc
import json
from collections.abc import Iterator

import pytest

from unfold.evaluation import suites
from unfold.evaluation.corpus import BASELINE
from unfold.evaluation.offline import baseline_from, check
from unfold.evaluation.suites import SUITES, SuiteResult


@pytest.fixture(scope="module", autouse=True)
def _release_fixture_projects() -> Iterator[None]:
    yield
    suites._fixture.cache_clear()
    gc.collect()  # keep this module's large object graphs out of later timing-sensitive tests


@pytest.fixture(scope="module")
async def results() -> list[SuiteResult]:
    return [await run() for run in SUITES.values()]


def test_baseline_file_covers_every_suite_metric(results: list[SuiteResult]) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    for r in results:
        assert set(baseline["suites"][r.name]) == set(r.metrics), r.name
        for spec in baseline["suites"][r.name].values():
            assert "min" in spec or "max" in spec


def test_scores_meet_the_baseline(results: list[SuiteResult]) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert check(results, baseline) == [], [r.failures for r in results]


def test_gate_trips_below_threshold() -> None:
    baseline = {"suites": {"retrieval": {"mrr": {"score": 0.8, "min": 0.75}}}}
    low = SuiteResult("retrieval", {"mrr": 0.5})
    assert check([low], baseline) == ["retrieval.mrr: 0.5 < min 0.75"]
    assert check([SuiteResult("retrieval", {})], baseline) == ["retrieval.mrr: not produced"]
    worse = {"suites": {"groundedness": {"hallucination_verified_rate": {"score": 0.0, "max": 0.05}}}}
    assert check([SuiteResult("groundedness", {"hallucination_verified_rate": 0.2})], worse)


def test_written_baseline_has_headroom_in_the_right_direction() -> None:
    written = baseline_from(
        [
            SuiteResult(
                "g", {"hallucination_verified_rate": 0.0, "mrr": 0.9, "scanner_false_positive_rate": 0.1}
            )
        ]
    )["suites"]["g"]
    assert written["mrr"]["min"] == 0.85
    assert written["hallucination_verified_rate"]["max"] == 0.05
    assert written["scanner_false_positive_rate"]["max"] == 0.15


async def test_evals_notice_a_broken_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suites, "untrusted", lambda text, source, trust="x": text)
    result = await suites.injection()
    assert result.metrics["fence_integrity"] < 1.0


async def test_evals_notice_missing_secret_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    from unfold.chat import answer

    monkeypatch.setattr(answer, "redact_text", lambda text: text)
    result = await suites.injection()
    assert result.metrics["chat_prompt_safety"] < 1.0


async def test_evals_notice_a_blind_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suites, "scan", lambda text, max_signals=20: [])
    result = await suites.injection()
    assert result.metrics["scanner_recall"] == 0.0


async def test_groundedness_notices_a_grader_that_verifies_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unfold.domain.enums import ClaimStatus

    def verify_all(claims, texts):  # type: ignore[no-untyped-def]
        return [c.model_copy(update={"status": ClaimStatus.VERIFIED}) for c in claims], {
            "claims": len(claims)
        }

    monkeypatch.setattr(suites, "grade_claims", verify_all)
    result = await suites.groundedness()
    assert result.metrics["hallucination_verified_rate"] == 1.0
    assert result.metrics["claim_grade_accuracy"] < 0.7
