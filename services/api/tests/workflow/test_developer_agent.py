"""Phase 5 exit criteria: the approved scope is implemented in a disposable workspace, reviewed
against policy, committed to an agent branch, and tested in the sandbox; the primary branch is
untouched."""

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
from tests.workflow.helpers import decide, pending, setup_project, start

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("unshare") or not Path(get_settings().sandbox_python).exists(),
    reason="needs the namespace sandbox (root, unshare, sandbox toolchain)",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def test_approved_scope_becomes_a_reviewed_commit_on_an_agent_branch(
    client: TestClient, project: dict
) -> None:
    ctx = setup_project(client, project)
    run_id = start(client, ctx)
    pid = ctx["pid"]
    arch = pending(client, pid, "reviewer")[0]
    assert decide(client, pid, arch["id"], "approved", "reviewer").status_code == 200
    drain()
    # Nothing is implemented before a human approves the implementation scope.
    assert client.get(f"/api/v1/projects/{pid}/code-changes?run_id={run_id}", headers=H("owner")).json() == []
    scope = pending(client, pid)[0]
    assert scope["gate"] == "implementation_scope"
    assert decide(client, pid, scope["id"], "approved", "owner").status_code == 200
    drain()

    changes = client.get(
        f"/api/v1/projects/{pid}/code-changes?run_id={run_id}&include_diff=true", headers=H("reviewer")
    ).json()
    first = changes[0]
    assert first["attempt"] == 1 and first["status"] == "applied"
    assert first["coding_agent"]["strategy"] == "scripted"  # labeled: no code model in this build
    assert first["review"]["ok"] is True and first["review"]["violations"] == []
    assert set(first["files"]) == {
        "task_service/errors.py",
        "task_service/service.py",
        "tests/test_title_validation.py",
    }
    assert first["branch"] == f"agent/{run_id[:8]}" and "diff --git" in first["diff"]

    tests = client.get(f"/api/v1/projects/{pid}/test-runs?run_id={run_id}", headers=H("qa")).json()
    dev = next(t for t in tests if t["suite"] == "developer" and t["attempt"] == 1)
    assert dev["status"] == "passed" and dev["commit"] == first["head_commit"]
    assert dev["sandbox"]["backend"] == "namespace"

    origin = get_settings().data_dir / "repositories" / ctx["repo_id"]
    assert _git(origin, "rev-parse", "main").stdout.strip() == first["base_commit"]
    branch_head = _git(origin, "rev-parse", f"agent/{run_id[:8]}").stdout.strip()
    assert _git(origin, "merge-base", "--is-ancestor", first["head_commit"], branch_head).returncode == 0
