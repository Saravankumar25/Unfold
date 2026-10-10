"""Offline evaluation runner (LLD §23): `python -m unfold.evaluation.offline [--suite NAME] [--gate]`.

Runs the suites in `unfold.evaluation.suites`, prints one line per metric, writes `evals/reports/offline.json`
(git-ignored) and, with `--gate`, exits non-zero when any metric is on the wrong side of its threshold in
`evals/baseline.json`. `--write-baseline` regenerates that file from the current scores (a deliberate act: review
the diff). No datastore, network or model key is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from unfold.core.config import Settings
from unfold.core.logging import configure_logging
from unfold.evaluation.corpus import BASELINE
from unfold.evaluation.suites import SUITES, SuiteResult

LOWER_IS_BETTER = frozenset({"hallucination_verified_rate", "scanner_false_positive_rate"})
MARGIN = 0.05  # headroom a freshly written baseline allows before the gate trips


def check(results: Sequence[SuiteResult], baseline: dict[str, Any]) -> list[str]:
    """Metrics that are missing or on the wrong side of their threshold."""
    problems: list[str] = []
    for r in results:
        for metric, spec in baseline.get("suites", {}).get(r.name, {}).items():
            value = r.metrics.get(metric)
            if value is None:
                problems.append(f"{r.name}.{metric}: not produced")
            elif "min" in spec and value < spec["min"]:
                problems.append(f"{r.name}.{metric}: {value} < min {spec['min']}")
            elif "max" in spec and value > spec["max"]:
                problems.append(f"{r.name}.{metric}: {value} > max {spec['max']}")
    return problems


def baseline_from(results: Sequence[SuiteResult]) -> dict[str, Any]:
    suites: dict[str, dict[str, dict[str, float]]] = {}
    for r in results:
        suites[r.name] = {}
        for metric, value in r.metrics.items():
            if metric in LOWER_IS_BETTER:
                suites[r.name][metric] = {"score": value, "max": round(min(1.0, value + MARGIN), 2)}
            else:
                suites[r.name][metric] = {"score": value, "min": round(max(0.0, value - MARGIN), 2)}
    return {"version": 1, "suites": suites}


async def run(names: Sequence[str]) -> list[SuiteResult]:
    return [await SUITES[name]() for name in names]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unfold.evaluation.offline")
    parser.add_argument("--suite", action="append", choices=sorted(SUITES), help="default: all suites")
    parser.add_argument("--gate", action="store_true", help="exit 1 when a metric violates the baseline")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)
    configure_logging(Settings(log_level="WARNING", log_format="console"))
    names = args.suite or list(SUITES)
    results = asyncio.run(run(names))

    for r in results:
        print(f"[{r.name}]")  # noqa: T201
        for metric, value in r.metrics.items():
            print(f"  {metric:<32} {value:.4f}")  # noqa: T201
        for failure in r.failures:
            print(f"  - {failure}")  # noqa: T201

    if args.write_baseline:
        if args.suite:
            parser.error("--write-baseline needs every suite")
        BASELINE.write_text(json.dumps(baseline_from(results), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {BASELINE}")  # noqa: T201
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    problems = check(results, baseline)
    report = BASELINE.parent / "reports" / "offline.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps({r.name: {"metrics": r.metrics, "failures": r.failures} for r in results}, indent=2),
        encoding="utf-8",
    )
    if problems:
        print("\nBELOW BASELINE:\n  " + "\n  ".join(problems))  # noqa: T201
    return 1 if (problems and args.gate) else 0


if __name__ == "__main__":
    sys.exit(main())
