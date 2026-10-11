"""The evaluation harness itself: datasets load, suites meet baselines, regressions are caught."""

from __future__ import annotations

import json
from typing import Any

from agentic_sdlc.evals.run import EVALS_DIR, check_baseline, main
from agentic_sdlc.evals.suites import SUITES, requirements_suite
from agentic_sdlc.requirements.rules import extract


def _data(name: str) -> dict[str, Any]:
    return json.loads((EVALS_DIR / name / "cases.json").read_text())


def test_every_suite_meets_its_baseline() -> None:
    baselines = json.loads((EVALS_DIR / "baselines.json").read_text())
    for name, suite in SUITES.items():
        result = suite(_data(name))
        assert result.results, name
        assert check_baseline(result, baselines[name]) == [], name


def test_a_regressed_extractor_is_caught() -> None:
    def no_injection_checks(blocks: list[dict[str, Any]], *, document_kind: str) -> dict[str, Any]:
        out = extract(blocks, document_kind=document_kind)
        out["questions"] = [q for q in out["questions"] if q["kind"] != "injection_warning"]
        return out

    baselines = json.loads((EVALS_DIR / "baselines.json").read_text())
    result = requirements_suite(_data("requirements"), extractor=no_injection_checks)
    failures = check_baseline(result, baselines["requirements"])
    assert any("injection_recall" in f for f in failures)


def test_cli_reports_and_exit_status(tmp_path: Any) -> None:
    report = tmp_path / "report.json"
    assert main(["--all", "--report", str(report)]) == 0
    body = json.loads(report.read_text())
    assert {s["suite"] for s in body["suites"]} == set(SUITES)
    assert body["config"]["strategy"] == "heuristic"
    # Criteria with no offline measurement are reported as such, never as met.
    unmeasured = [c for c in body["skill_criteria"] if c["measured"] is None]
    assert unmeasured and all(c["status"] == "not measured offline" for c in unmeasured)


def test_cli_fails_when_a_baseline_is_raised(tmp_path: Any) -> None:
    for name in SUITES:
        (tmp_path / name).mkdir()
        (tmp_path / name / "cases.json").write_text((EVALS_DIR / name / "cases.json").read_text())
    (tmp_path / "baselines.json").write_text(
        json.dumps({"requirements": {"contradiction_recall": {"min": 1.0}}})
    )
    assert main(["--suite", "requirements", "--evals-dir", str(tmp_path)]) == 1


def test_recorded_runs_are_listed(client: Any) -> None:
    from tests.conftest import H

    assert main(["--suite", "redaction", "--record"]) == 0
    rows = client.get("/api/v1/evaluations", headers=H("auditor")).json()
    assert rows[0]["suite"] == "redaction" and rows[0]["passed"] is True
    assert rows[0]["config"]["strategy"] == "heuristic" and rows[0]["failing_cases"] == []
    assert client.get("/api/v1/evaluations").status_code == 401
