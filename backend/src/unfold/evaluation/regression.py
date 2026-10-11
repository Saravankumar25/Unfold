"""Guide-quality regression gate (phase 5 task 9): rescore a guide and compare it with the recorded v2 numbers.

Deterministic, no model, no network. The same function judges a stored guide in CI (`tests/unit/
test_quality_regression.py`) and a freshly regenerated one in the on-demand `-m llm` test. Thresholds are the
investigation-engine gate (`docs/design/investigation-engine.md` section 7) plus two that the stored numbers make
checkable: a guide may not silently lose its citations, and may not repeat a prompt-injection payload.

    python -m unfold.evaluation.regression [baseline_dir...]    # default: evals/baselines/pipeline-v2 (+ replicate)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from unfold.evaluation import rubric
from unfold.evaluation.corpus import REPO_ROOT
from unfold.guide.models import GuideSection
from unfold.security.injection import scan

BASELINE = REPO_ROOT / "evals" / "baselines" / "pipeline-v2"
TOLERANCE = 0.3  # a dimension may move this far from the recorded number (0-4 scale)
MIN_RESOLVE_RATE = 0.98
MIN_CITATION_SHARE = 0.8  # of the citations the recorded guide made; resolving 3 of 3 is not "all cited"


def problems(
    sections: Sequence[GuideSection],
    current: Mapping[str, Any],
    recorded: Mapping[str, Any],
    *,
    one_sided: bool = False,
) -> list[str]:
    """What is wrong with a scored guide, as sentences. `current` and `recorded` are `score_guide` outputs.

    `one_sided`: only a drop below the recorded number counts (a regenerated guide may be better, a stored one
    must not move at all beyond the tolerance either way, because moving means the scorer or the data changed).
    """
    out: list[str] = []
    for dim, base in recorded["scores"].items():
        delta = current["scores"][dim] - base
        if delta < -TOLERANCE or (not one_sided and delta > TOLERANCE):
            out.append(f"{dim}: {current['scores'][dim]} vs recorded {base} (tolerance {TOLERANCE})")
    raw = current["raw"]
    cit = raw["citation_accuracy"]
    rate = cit["resolve_rate"]
    if rate is None or rate < MIN_RESOLVE_RATE:
        out.append(f"citation resolve rate {rate} < {MIN_RESOLVE_RATE} ({cit['citations']} citations)")
    floor = MIN_CITATION_SHARE * recorded["raw"]["citation_accuracy"]["citations"]
    if cit["citations"] < floor:
        out.append(
            f"{cit['citations']} citations, fewer than {MIN_CITATION_SHARE:.0%} of the recorded guide's"
        )
    for field in ("unsupported", "uncited"):
        if raw["groundedness"][field]:
            out.append(f"{raw['groundedness'][field]} project facts are {field}")
    if raw["uncertainty_honesty"]["unverified_facts_left"]:
        out.append(
            f"{raw['uncertainty_honesty']['unverified_facts_left']} unverified project facts left standing"
        )
    if signals := scan(rubric._guide_text(sections)):
        out.append("guide repeats prompt-injection text: " + ", ".join(sorted({s.rule for s in signals})))
    return out


def check_dir(path: Path, recorded: Mapping[str, Any]) -> list[str]:
    """Rescore the stored guide in `path` against its entry in a `scores.json`."""
    sections, ir, chunks = rubric.load_dir(path)
    return problems(sections, rubric.score_guide(sections, ir, chunks), recorded)


def main(argv: Sequence[str] | None = None) -> int:
    roots = [Path(a) for a in (argv if argv is not None else sys.argv[1:])] or [
        BASELINE,
        BASELINE / "replicate",
    ]
    failed = 0
    for root in roots:
        scores = json.loads((root / "scores.json").read_text(encoding="utf-8"))
        for name, recorded in scores.items():
            found = check_dir(root / name, recorded)
            failed += bool(found)
            print(f"{'FAIL' if found else 'ok  '} {root.name}/{name}" + "".join(f"\n  - {p}" for p in found))  # noqa: T201
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
