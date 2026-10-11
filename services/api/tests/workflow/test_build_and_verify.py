"""Phase 5-6 exit criteria: isolated changes, real test execution, criterion verification, linked defects,
bounded repair loop, and an untouched primary repository."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.workflows.worker import drain
from tests.conftest import H
from tests.workflow.helpers import decide, pending, run, setup_project, start

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("unshare") or not Path(get_settings().sandbox_python).exists(),
    reason="needs the namespace sandbox (root, unshare, sandbox toolchain)",
)


def approve_both_gates(client: TestClient, pid: str) -> None:
    arch = pending(client, pid, "reviewer")[0]
    assert decide(client, pid, arch["id"], "approved", "reviewer").status_code == 200
    drain()
    scope = pending(client, pid)[0]
    assert scope["gate"] == "implementation_scope"
    assert decide(client, pid, scope["id"], "approved", "owner").status_code == 200
    drain()


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_defect_repair_loop_to_verified_change(client: TestClient, project: dict) -> None:
    ctx = setup_project(client, project)
    run_id = start(client, ctx)
    pid = ctx["pid"]
    repos = client.get(f"/api/v1/projects/{pid}/repositories", headers=H("owner")).json()
    assert repos[0]["is_sample"] is True
    approve_both_gates(client, pid)

    r = run(client, pid, run_id)
    assert r["status"] != "escalated", r["outcome"].get("escalation")
    assert r["counters"]["attempt"] == 2 and r["counters"]["repairs"] == 1

    changes = client.get(
        f"/api/v1/projects/{pid}/code-changes?run_id={run_id}&include_diff=true", headers=H("reviewer")
    ).json()
    assert [c["attempt"] for c in changes] == [1, 2] and all(c["status"] == "applied" for c in changes)
    assert changes[0]["coding_agent"]["strategy"] == "scripted"
    assert changes[0]["review"]["ok"] is True
    assert set(changes[0]["files"]) == {
        "task_service/errors.py",
        "task_service/service.py",
        "tests/test_title_validation.py",
    }
    assert changes[1]["base_commit"] == changes[0]["head_commit"]

    runs_ = client.get(f"/api/v1/projects/{pid}/test-runs?run_id={run_id}", headers=H("qa")).json()
    summary = [(t["suite"], t["attempt"], t["status"]) for t in runs_]
    assert summary[:2] == [("developer", 1, "passed"), ("acceptance", 1, "failed")]
    assert ("acceptance", 2, "passed") in summary and ("regression", 2, "passed") in summary
    acc1 = next(t for t in runs_ if t["suite"] == "acceptance" and t["attempt"] == 1)
    assert acc1["sandbox"]["backend"] == "namespace" and acc1["commit"] == changes[0]["head_commit"]
    failing = [c for c in acc1["cases"] if c["outcome"] == "failed"]
    assert [c["criteria"] for c in failing] == [["FR-001-AC2"]]
    detail = client.get(f"/api/v1/projects/{pid}/test-runs/{acc1['id']}", headers=H("qa")).json()
    assert "1 failed" in detail["log"]

    verdicts = client.get(f"/api/v1/projects/{pid}/verification?run_id={run_id}", headers=H("auditor")).json()
    first = {v["criterion_key"]: v["status"] for v in verdicts if v["attempt"] == 1}
    final = {v["criterion_key"]: v["status"] for v in verdicts if v["attempt"] == 2}
    assert first["FR-001-AC2"] == "failed" and first["FR-001-AC1"] == "verified"
    assert final == {
        "FR-001-AC1": "verified",
        "FR-001-AC2": "verified",
        "FR-002-AC1": "verified",
        "FR-003-AC1": "verified",
        "NFR-001-AC1": "unverifiable",
        "NFR-002-AC1": "verified",
    }

    defects = client.get(f"/api/v1/projects/{pid}/defects?run_id={run_id}", headers=H("qa")).json()
    assert len(defects) == 1
    d = defects[0]
    assert d["criterion_key"] == "FR-001-AC2" and d["severity"] == "major"
    assert d["status"] == "verified" and d["found_in_attempt"] == 1 and d["fixed_in_attempt"] == 2
    assert any("pytest" in step for step in d["reproduction_steps"])
    backlog = client.get(f"/api/v1/projects/{pid}/backlog", headers=H("owner")).json()["items"]
    bug = next(i for i in backlog if i["kind"] == "bug")
    assert (
        bug["key"] == d["backlog_key"] and bug["status"] == "done" and bug["criteria_keys"] == ["FR-001-AC2"]
    )
    stories = [i for i in backlog if i["kind"] == "story"]
    assert all(s["status"] == "done" and s["actuals"]["attempts"] == 2 for s in stories)

    # The primary repository's main branch is untouched; the work lives on the agent branch.
    origin = next(iter((get_settings().data_dir / "repositories").glob(f"{repos[0]['id']}")))
    assert _git(str(origin), "rev-parse", "main") == changes[0]["base_commit"]
    assert _git(str(origin), "rev-parse", f"agent/{run_id[:8]}") == changes[1]["head_commit"]

    trace = client.get(f"/api/v1/projects/{pid}/traceability?run_id={run_id}", headers=H("auditor")).json()
    row = next(x for x in trace["rows"] if x["criterion"] == "FR-001-AC2")
    assert row["source"]["block_id"] and row["verification"]["status"] == "verified"
    assert row["defects"] == [{"title": d["title"], "status": "verified"}] and row["code_changes"]


def test_repair_limit_escalates_with_open_defect(client: TestClient, project: dict) -> None:
    ctx = setup_project(client, project, limits={"max_repair_attempts": 0})
    run_id = start(client, ctx)
    approve_both_gates(client, ctx["pid"])
    r = run(client, ctx["pid"], run_id)
    assert r["status"] == "escalated" and r["current_stage"] == "create_defects"
    assert "repair limit reached" in r["outcome"]["escalation"]["reason"]
    open_ = client.get(f"/api/v1/projects/{ctx['pid']}/defects?status=open", headers=H("qa")).json()
    assert len(open_) == 1 and open_[0]["criterion_key"] == "FR-001-AC2"
