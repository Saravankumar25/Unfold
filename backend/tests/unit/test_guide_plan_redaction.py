"""plan_guide redacts repository facts and the reader instruction before they reach a prompt (audit P2-d)."""

from __future__ import annotations

from typing import Any, cast

from unfold.guide.generate import plan_guide
from unfold.guide.models import GuidePlan, GuideSectionPlan
from unfold.llm.schemas import LLMRequest
from unfold.security.secrets import REDACTED

TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


class _CapturingLLM:
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def generate_structured(self, req: LLMRequest, schema: type[Any]) -> Any:
        self.requests.append(req)
        return GuidePlan(
            estimated_read_minutes=6,
            sections=[
                GuideSectionPlan(
                    key=k, title=k, kind="core", purpose=k, target_minutes=2, evidence_objectives=[k]
                )
                for k in ("one", "two", "three")
            ],
        )


async def test_facts_and_instruction_are_redacted_before_the_prompt_is_built() -> None:
    llm = _CapturingLLM()
    await plan_guide(
        cast(Any, llm),
        cast(Any, None),
        [f"Config sets token = '{TOKEN}'"],
        technical_level="intermediate",
        instruction=f"focus on auth, my key is {TOKEN}",
        job_id="j",
        project_id="p",
    )
    prompt = llm.requests[0].messages[0].content
    assert TOKEN not in prompt
    assert prompt.count(REDACTED) == 2
