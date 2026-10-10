from __future__ import annotations

import pytest

from agentic_sdlc.backlog.graph import critical_path, find_cycle, topological_layers, would_create_cycle
from agentic_sdlc.backlog.planning import analyze_dependencies, calibration_factor, decompose, estimate_story
from agentic_sdlc.documents.normalize import normalize_docx
from agentic_sdlc.requirements.rules import extract
from tests.helpers import SAMPLE_BRD


def test_find_cycle() -> None:
    assert find_cycle("abc", [("a", "b"), ("b", "c")]) is None
    cyc = find_cycle("abc", [("a", "b"), ("b", "c"), ("c", "a")])
    assert cyc is not None and cyc[0] == cyc[-1] and set(cyc) == {"a", "b", "c"}
    assert find_cycle("a", [("a", "a")]) == ["a", "a"]


def test_would_create_cycle() -> None:
    edges = [("a", "b"), ("b", "c")]
    assert would_create_cycle(edges, ("c", "a")) is not None
    assert would_create_cycle(edges, ("a", "c")) is None


def test_topological_layers_and_errors() -> None:
    assert topological_layers("abcd", [("c", "a"), ("c", "b"), ("d", "c")]) == [["a", "b"], ["c"], ["d"]]
    with pytest.raises(ValueError, match="cycle"):
        topological_layers("ab", [("a", "b"), ("b", "a")])
    with pytest.raises(ValueError, match="unknown"):
        topological_layers("a", [("a", "z")])


def test_critical_path_uses_weights() -> None:
    # c depends on a (weight 5) or b (weight 1); the longest chain goes through a.
    assert critical_path("abc", [("c", "a"), ("c", "b")], {"a": 5, "b": 1, "c": 2}) == ["a", "c"]


def _sample_reqs() -> list[dict]:
    e = extract(normalize_docx(SAMPLE_BRD.read_bytes())["blocks"], document_kind="brd")
    return [
        {
            **r,
            "criteria": [
                {"key": f"{r['key']}-AC{i + 1}", **c} for i, c in enumerate(r["acceptance_criteria"])
            ],
        }
        for r in e["requirements"]
    ]


def test_decompose_sample() -> None:
    plan = decompose(_sample_reqs(), epic_title="Task title validation", project_name="Demo")
    assert plan["epic"]["title"] == "Task title validation"
    assert [f["title"] for f in plan["features"]] == ["Functional behaviour", "Quality attributes"]
    assert [s["requirement_keys"][0] for s in plan["stories"]] == [
        "FR-001",
        "FR-002",
        "FR-003",
        "NFR-001",
        "NFR-002",
    ]
    assert plan["dependencies"] == [
        {"from_ref": "S-FR-003", "to_ref": "S-FR-002", "reason": "FR-003 references FR-002"}
    ]
    analysis = analyze_dependencies(plan["stories"], plan["dependencies"])
    assert analysis["valid"] and analysis["layers"][0][:2] == ["S-FR-001", "S-FR-002"]
    assert "S-FR-003" in analysis["layers"][1]


def test_estimates_keep_dimensions_independent() -> None:
    req = _sample_reqs()[0]
    small = estimate_story({}, req, open_questions=0, repo_context_tokens=1_000)
    large = estimate_story({}, req, open_questions=0, repo_context_tokens=50_000)
    # Token consumption depends on repository context; complexity (story points) does not.
    assert large["estimate"]["tokens_in"] > small["estimate"]["tokens_in"]
    assert large["story_points"] == small["story_points"]
    assert small["estimate"]["is_estimated"] is True and small["estimate"]["method"]
    # Cost follows pricing only.
    priced = estimate_story({}, req, open_questions=0, repo_context_tokens=1_000, price_in_per_1k=0.01)
    assert priced["estimate"]["cost_usd"] > 0 and small["estimate"]["cost_usd"] == 0
    assert priced["story_points"] == small["story_points"]


def test_uncertainty_from_open_questions() -> None:
    req = _sample_reqs()[3]  # NFR-001 "should be fast", inferred criterion
    calm = estimate_story({}, req, open_questions=0, repo_context_tokens=1000)
    unsure = estimate_story({}, req, open_questions=2, repo_context_tokens=1000)
    assert calm["uncertainty"] in ("low", "medium") and unsure["uncertainty"] == "high"
    assert unsure["estimate"]["tokens_range"][1] > calm["estimate"]["tokens_range"][1]


def test_calibration_requires_history() -> None:
    assert calibration_factor([(100, 200)]) == (1.0, 1)
    assert calibration_factor([(100, 200), (100, 300), (100, 100)]) == (2.0, 3)
    assert calibration_factor([(100, 10_000)] * 5) == (4.0, 5)  # clamped
