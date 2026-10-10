"""Claims the writer listed but the chapter never makes (seen on the fastapi_rag run): not statements of fact."""

from __future__ import annotations

from unfold.domain.enums import ClaimKind, ClaimStatus
from unfold.domain.models import ClaimRecord
from unfold.guide.generate import unstated_claims

CHUNKS = {"c1": "class LLMClient:\n    def __init__(self, settings):\n        self.settings = settings\n"}
PROSE = (
    "Services such as the HybridRetriever and LLMClient retrieve these runtime settings with get_settings."
)


def _claim(
    text: str, status: ClaimStatus, ids: list[str] | None = None, kind: ClaimKind = ClaimKind.PROJECT_FACT
) -> ClaimRecord:
    return ClaimRecord(text=text, kind=kind, status=status, evidence_ids=ids if ids is not None else ["c1"])


def test_a_claim_whose_unverified_name_the_chapter_never_mentions_is_not_standing_as_fact() -> None:
    phantom = _claim(
        "LLMClient initializes its asynchronous OpenAI client using settings.", ClaimStatus.INFERRED
    )
    assert unstated_claims([phantom], CHUNKS, PROSE) == [phantom]


def test_a_claim_whose_unverified_name_is_in_the_prose_stays_a_claim() -> None:
    said = _claim("LLMClient initializes its OpenAI client using settings.", ClaimStatus.INFERRED)
    assert unstated_claims([said], CHUNKS, PROSE + " It then builds the OpenAI client.") == []


def test_claims_with_nothing_to_look_for_are_never_dropped() -> None:
    plain = _claim("It retries failed calls with exponential backoff.", ClaimStatus.UNSUPPORTED, ids=[])
    verified = _claim("`LLMClient` takes settings.", ClaimStatus.VERIFIED)
    inference = _claim("The OpenAI client is probably async.", ClaimStatus.INFERRED, kind=ClaimKind.INFERENCE)
    assert unstated_claims([plain, verified, inference], CHUNKS, PROSE) == []


def test_an_uncited_claim_that_names_something_the_chapter_lacks_is_dropped() -> None:
    uncited = _claim("`NonexistentWidgetFactory` builds widgets.", ClaimStatus.UNSUPPORTED, ids=[])
    assert unstated_claims([uncited], CHUNKS, PROSE) == [uncited]
    assert unstated_claims([uncited], CHUNKS, PROSE + " NonexistentWidgetFactory builds widgets.") == []
