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


def test_a_claim_naming_get_db_is_stated_by_prose_that_writes_getdb() -> None:
    claim = _claim("`get_db` opens the session.", ClaimStatus.INFERRED)
    assert unstated_claims([claim], CHUNKS, "The route calls getDb to open the session.") == []
    camel = _claim("`getDb` opens the session.", ClaimStatus.INFERRED)
    assert unstated_claims([camel], CHUNKS, "The route calls get_db to open the session.") == []


def test_snake_and_camel_spellings_are_one_name_when_grading_and_placing() -> None:
    from unfold.guide.generate import grade_claims
    from unfold.guide.models import ParagraphBlock, SectionContent
    from unfold.guide.verify import place_claims

    chunks = {"c1": "def getDb():\n    return session\n"}
    graded, _ = grade_claims([_claim("`get_db` returns the session.", ClaimStatus.PROPOSED)], chunks)
    assert graded[0].status is ClaimStatus.VERIFIED
    content = SectionContent(
        summary="s",
        blocks=[ParagraphBlock(text="Settings are loaded once."), ParagraphBlock(text="getDb returns the session.")],
    )
    assert place_claims(content, ["`get_db` returns the session."]) == {"`get_db` returns the session.": "b1"}
