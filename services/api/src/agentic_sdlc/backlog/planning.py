"""Deterministic planning rules (the planner skills' `heuristic` strategies).

Estimates are produced on separate, independent dimensions and never derived from each other:
- complexity → story points (relative, Fibonacci)
- uncertainty → low / medium / high
- model consumption → input/output tokens (estimated; method recorded)
- financial cost → tokens times the routed model's price
- wall-clock → tokens / assumed throughput + fixed verification overhead
"""

from __future__ import annotations

import math
import re
from statistics import median
from typing import Any

from agentic_sdlc.backlog.graph import critical_path, find_cycle, topological_layers

KEY_REF = re.compile(r"\b(?:FR|NFR|BR|REQ|US|SR|CON|TR)-\d{3,4}(?:-D\d+)?\b")
FIB = [1, 2, 3, 5, 8, 13]
PRIORITY = {"must": 1, "should": 2, "could": 3}

TOKEN_MODEL_VERSION = "heuristic-token-model/1"  # noqa: S105 - not a secret
ASSUMED_OUTPUT_TOKENS_PER_S = 30.0  # conservative single-GPU 7B decode rate; stated, not measured
VERIFICATION_OVERHEAD_S = 120.0  # sandboxed test run + verification per story


def decompose(requirements: list[dict[str, Any]], *, epic_title: str, project_name: str) -> dict[str, Any]:
    """One epic; features grouped by requirement kind; one story per requirement; explicit
    cross-references ("see FR-002") become dependencies."""
    feature_titles = {
        "functional": "Functional behaviour",
        "non_functional": "Quality attributes",
        "constraint": "Constraints",
    }
    features: list[dict[str, Any]] = []
    for kind in ("functional", "non_functional", "constraint"):
        if any(r["kind"] == kind for r in requirements):
            features.append(
                {
                    "ref": f"F-{kind}",
                    "title": feature_titles[kind],
                    "description": f"{feature_titles[kind]} for {epic_title}.",
                }
            )
    stories: list[dict[str, Any]] = []
    keys = {r["key"] for r in requirements}
    deps: list[dict[str, Any]] = []
    for r in requirements:
        ref = f"S-{r['key']}"
        title = r["title"] if len(r["title"]) > 12 else r["statement"][:100]
        stories.append(
            {
                "ref": ref,
                "kind": "story",
                "title": title[:1].upper() + title[1:],
                "description": r["statement"],
                "feature_ref": f"F-{r['kind']}",
                "requirement_keys": [r["key"]],
                "criteria_keys": [c["key"] for c in r.get("criteria", [])],
                "priority": PRIORITY.get(r.get("priority", "should"), 2),
                "labels": [r["kind"].replace("_", "-"), f"req:{r['key']}"],
            }
        )
        for ref_key in sorted(set(KEY_REF.findall(r["statement"])) - {r["key"]}):
            if ref_key in keys:
                deps.append(
                    {"from_ref": ref, "to_ref": f"S-{ref_key}", "reason": f"{r['key']} references {ref_key}"}
                )
    return {
        "epic": {
            "title": epic_title,
            "description": f"Deliver the requirements captured for {project_name}.",
            "labels": ["epic"],
        },
        "features": features,
        "stories": stories,
        "dependencies": deps,
    }


def _fib(x: float) -> int:
    return next((f for f in FIB if f >= x), FIB[-1])


def calibration_factor(history: list[tuple[int, int]]) -> tuple[float, int]:
    """Median ratio actual/estimated total tokens over past stories (needs ≥3 samples)."""
    ratios = [a / e for e, a in history if e > 0 and a > 0]
    if len(ratios) < 3:
        return 1.0, len(ratios)
    return round(min(4.0, max(0.25, median(ratios))), 3), len(ratios)


def estimate_story(
    story: dict[str, Any],
    requirement: dict[str, Any],
    *,
    open_questions: int,
    repo_context_tokens: int,
    calibration: float = 1.0,
    price_in_per_1k: float = 0.0,
    price_out_per_1k: float = 0.0,
) -> dict[str, Any]:
    criteria = requirement.get("criteria", [])
    n_criteria = max(1, len(criteria))
    inferred = sum(1 for c in criteria if c.get("origin") == "inferred")
    words = len(requirement["statement"].split())
    refs = len(set(KEY_REF.findall(requirement["statement"])) - {requirement["key"]})

    complexity = 1.0 + 0.8 * (n_criteria - 1) + words / 25 + 0.5 * refs
    if requirement["kind"] == "non_functional":
        complexity += 1.0
    points = _fib(complexity)

    risk = open_questions * 2 + inferred + (1 if requirement.get("origin") == "inferred" else 0)
    uncertainty = "low" if risk == 0 else ("medium" if risk <= 2 else "high")
    spread = {"low": 1.2, "medium": 1.6, "high": 2.5}[uncertainty]

    req_text_tokens = math.ceil((len(requirement["statement"]) + sum(len(c["text"]) for c in criteria)) / 4)
    per_attempt_in = req_text_tokens + repo_context_tokens + 1500  # instructions + tool schemas
    per_attempt_out = 900 + 400 * n_criteria  # patch + tests per criterion
    expected_attempts = 1.0 + {"low": 0.3, "medium": 0.6, "high": 1.0}[uncertainty]
    tokens_in = int(per_attempt_in * expected_attempts * calibration)
    tokens_out = int(per_attempt_out * expected_attempts * calibration)
    cost = round(tokens_in / 1000 * price_in_per_1k + tokens_out / 1000 * price_out_per_1k, 4)
    duration_s = int(tokens_out / ASSUMED_OUTPUT_TOKENS_PER_S + VERIFICATION_OVERHEAD_S * expected_attempts)
    return {
        "story_points": points,
        "uncertainty": uncertainty,
        "estimate": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_range": [int((tokens_in + tokens_out) / spread), int((tokens_in + tokens_out) * spread)],
            "cost_usd": cost,
            "duration_s": duration_s,
            "duration_range_s": [int(duration_s / spread), int(duration_s * spread)],
            "expected_attempts": expected_attempts,
            "is_estimated": True,
            "method": TOKEN_MODEL_VERSION,
            "calibration_factor": calibration,
            "assumptions": {
                "output_tokens_per_s": ASSUMED_OUTPUT_TOKENS_PER_S,
                "verification_overhead_s": VERIFICATION_OVERHEAD_S,
                "repo_context_tokens": repo_context_tokens,
            },
            "drivers": {
                "criteria": n_criteria,
                "inferred_criteria": inferred,
                "open_questions": open_questions,
                "references": refs,
                "words": words,
            },
        },
    }


def analyze_dependencies(stories: list[dict[str, Any]], deps: list[dict[str, Any]]) -> dict[str, Any]:
    refs = [s["ref"] for s in stories]
    edges = [(d["from_ref"], d["to_ref"]) for d in deps]
    cycle = find_cycle(refs, edges)
    if cycle:
        return {"valid": False, "cycle": cycle, "layers": [], "milestones": [], "critical_path": []}
    layers = topological_layers(refs, edges)
    points = {s["ref"]: float(s.get("story_points") or 1) for s in stories}
    return {
        "valid": True,
        "cycle": None,
        "layers": layers,
        "milestones": [{"name": f"Milestone {i + 1}", "refs": layer} for i, layer in enumerate(layers)],
        "critical_path": critical_path(refs, edges, points),
    }
