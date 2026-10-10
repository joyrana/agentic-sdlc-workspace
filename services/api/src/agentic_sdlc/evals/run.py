"""Run offline evaluation suites and compare them with the committed baselines.

    python -m agentic_sdlc.evals.run --all [--record] [--report PATH]
    python -m agentic_sdlc.evals.run --suite requirements

Exit status is 1 when any metric falls outside its baseline bound, so CI can gate on it. Baselines
live in `evals/baselines.json`; they are raised deliberately in a reviewed change, never lowered to
make a run pass. `--record` stores each suite as an EvaluationRun row (needs DATABASE_URL).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_sdlc.core.settings import REPO_ROOT
from agentic_sdlc.evals.suites import SUITES, SuiteResult

EVALS_DIR = REPO_ROOT / "evals"
# The components each suite exercises; recorded so a result can be tied to what produced it.
SUITE_SKILLS = {
    "requirements": ["brd-analysis", "transcript-analysis"],
    "planning": ["backlog-decomposition", "dependency-analysis"],
    "patch_policy": ["code-generation"],
    "release_policy": ["release-readiness"],
    "redaction": [],
}


def check_baseline(result: SuiteResult, bounds: dict[str, dict[str, float]]) -> list[str]:
    failures = []
    for metric, bound in bounds.items():
        value = result.metrics.get(metric)
        if value is None:
            failures.append(f"{result.suite}.{metric}: metric missing")
            continue
        if "min" in bound and value < bound["min"]:
            failures.append(f"{result.suite}.{metric} = {value} is below the baseline minimum {bound['min']}")
        if "max" in bound and value > bound["max"]:
            failures.append(f"{result.suite}.{metric} = {value} is above the baseline maximum {bound['max']}")
    return failures


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _skill_versions(suite: str) -> dict[str, str]:
    from agentic_sdlc.skills.registry import get_skill_registry

    reg = get_skill_registry()
    return {s: reg.get(s).version for s in SUITE_SKILLS.get(suite, []) if s in reg.manifests}


def skill_criteria(results: list[SuiteResult]) -> list[dict[str, Any]]:
    """Each skill's declared success criteria against what the offline suites measured.

    Criteria named `*_max` are upper bounds. A criterion no suite measures is reported as such,
    not counted as met.
    """
    from agentic_sdlc.skills.registry import get_skill_registry

    by_suite = {r.suite: r for r in results}
    rows = []
    for m in sorted(get_skill_registry().manifests.values(), key=lambda m: m.id):
        suite = m.evaluation.suite
        for name, threshold in m.evaluation.success_criteria.items():
            metric = name.removesuffix("_max")
            measured = by_suite[suite].metrics.get(metric) if suite in by_suite else None
            if measured is None:
                status = "not measured offline"
            elif name.endswith("_max"):
                status = "met" if measured <= threshold else "not met"
            else:
                status = "met" if measured >= threshold else "not met"
            rows.append(
                {
                    "skill": m.ref,
                    "suite": suite,
                    "criterion": name,
                    "threshold": threshold,
                    "measured": measured,
                    "status": status,
                }
            )
    return rows


def _record(results: list[SuiteResult], failures: dict[str, list[str]], config: dict[str, Any]) -> None:
    from agentic_sdlc.core.db import session_scope
    from agentic_sdlc.orm.ai import EvaluationRun

    with session_scope() as db:
        for r in results:
            db.add(
                EvaluationRun(
                    suite=r.suite,
                    dataset_version=r.dataset_version,
                    config={**config, "skills": _skill_versions(r.suite)},
                    metrics=r.metrics,
                    results=r.results,
                    passed=not failures[r.suite],
                    cost_usd=0.0,
                    notes="; ".join(failures[r.suite]) or "within baseline",
                )
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="run every suite")
    group.add_argument("--suite", choices=sorted(SUITES), action="append", help="run one suite (repeatable)")
    parser.add_argument("--record", action="store_true", help="store results as EvaluationRun rows")
    parser.add_argument("--report", type=Path, help="write a JSON report to this path")
    parser.add_argument("--evals-dir", type=Path, default=EVALS_DIR)
    args = parser.parse_args(argv)

    names = sorted(SUITES) if args.all else sorted(set(args.suite))
    baselines = json.loads((args.evals_dir / "baselines.json").read_text())
    results: list[SuiteResult] = []
    failures: dict[str, list[str]] = {}
    for name in names:
        data = json.loads((args.evals_dir / name / "cases.json").read_text())
        result = SUITES[name](data)
        results.append(result)
        failures[name] = check_baseline(result, baselines.get(name, {}))
        failed_cases = [c["id"] for c in result.results if not c["ok"]]
        status = "ok" if not failures[name] else "BELOW BASELINE"
        print(f"{name} (dataset {result.dataset_version}, {len(result.results)} cases): {status}")
        for metric, value in result.metrics.items():
            print(f"  {metric:36} {value}")
        if failed_cases:
            print(f"  failing cases: {', '.join(failed_cases)}")
        for f in failures[name]:
            print(f"  ! {f}")

    criteria = skill_criteria(results)
    print("skill success criteria:")
    for c in criteria:
        print(
            f"  {c['skill']:36} {c['criterion']:26} {c['status']} (threshold {c['threshold']}, "
            f"measured {c['measured']})"
        )
    unmet = [c for c in criteria if c["status"] == "not met"]
    for c in unmet:
        failures.setdefault(c["suite"], []).append(f"{c['skill']} {c['criterion']} not met")

    config = {"strategy": "heuristic", "git_sha": _git_sha(), "model": None}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "config": config,
                    "suites": [asdict(r) | {"baseline_failures": failures[r.suite]} for r in results],
                    "skill_criteria": criteria,
                },
                indent=2,
            )
        )
    if args.record:
        _record(results, failures, config)
    return 1 if any(failures.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
