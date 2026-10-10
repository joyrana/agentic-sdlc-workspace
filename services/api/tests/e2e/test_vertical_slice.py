"""The first vertical slice (master prompt §26), end to end through the HTTP API.

Real PostgreSQL, real LangGraph checkpoints, real sandboxed test execution, real git operations,
real staging deployment with smoke and health checks. The only scripted element is the coding agent
for the sample repository (no inference hardware), and it is labeled as such in every record.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.workflows.worker import drain
from tests.conftest import EMAILS, H
from tests.helpers import SAMPLE_BRD
from tests.workflow.helpers import decide, pending, run

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("unshare") or not Path(get_settings().sandbox_python).exists(),
    reason="needs the namespace sandbox",
)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_document_to_staging_release(client: TestClient) -> None:
    # 1. Create a project (the creator becomes owner) and staff it.
    p = client.post(
        "/api/v1/projects",
        json={"name": "Task Service Delivery", "goals": "Predictable titles"},
        headers=H("owner"),
    ).json()
    pid = p["id"]
    for who, role in [
        ("engineer", "engineer"),
        ("reviewer", "reviewer"),
        ("qa", "qa"),
        ("approver", "release_approver"),
        ("auditor", "auditor"),
    ]:
        assert (
            client.post(
                f"/api/v1/projects/{pid}/members",
                json={"email": EMAILS[who], "role": role},
                headers=H("owner"),
            ).status_code
            == 201
        )

    # 2. Upload the sample BRD.
    up = client.post(
        f"/api/v1/projects/{pid}/documents",
        data={"kind": "brd"},
        files={"file": ("brd.docx", SAMPLE_BRD.read_bytes(), DOCX)},
        headers=H("engineer"),
    )
    assert up.status_code == 202
    drain()

    # 3. Requirements with source references.
    reqs = client.get(f"/api/v1/projects/{pid}/requirements", headers=H("reviewer")).json()
    assert len(reqs) == 5 and all(r["locator"].get("block_id") for r in reqs)

    # 4. Review and edit an acceptance criterion (the vague NFR gets a measurable target).
    nfr1 = next(r for r in reqs if r["key"] == "NFR-001")
    crit = nfr1["criteria"][0]
    edited = client.patch(
        f"/api/v1/projects/{pid}/acceptance-criteria/{crit['id']}",
        json={
            "expected_version": crit["version"],
            "text": "Creating a task completes in under 50 ms on the reference machine.",
        },
        headers=H("reviewer"),
    )
    assert edited.status_code == 200 and edited.json()["origin"] == "human"

    # 5-6. Register the sample repository and start delivery (planning + repository inspection run).
    repo = client.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"name": "sample-task-service", "kind": "fixture", "location": "sample-task-service"},
        headers=H("owner"),
    ).json()
    run_id = client.post(
        f"/api/v1/projects/{pid}/runs", json={"repository_id": repo["id"]}, headers=H("engineer")
    ).json()["id"]
    drain()
    backlog = client.get(f"/api/v1/projects/{pid}/backlog", headers=H("owner")).json()
    assert [i["kind"] for i in backlog["items"]].count("story") == 5
    assert backlog["analysis"]["valid"]

    # 7-8. Architecture proposal + ADR, approved by a human reviewer.
    r = run(client, pid, run_id)
    assert r["status"] == "awaiting_approval" and r["current_stage"] == "architecture_gate"
    assert client.get(f"/api/v1/projects/{pid}/adrs", headers=H("reviewer")).json()[0]["number"] == 1
    arch = pending(client, pid, "reviewer")[0]
    assert (
        decide(client, pid, arch["id"], "approved", "reviewer", "Matches the service conventions").status_code
        == 200
    )
    drain()
    scope = pending(client, pid)[0]
    assert decide(client, pid, scope["id"], "approved", "owner").status_code == 200
    drain()

    # 9-12. Isolated change, automated tests, verification, defect and bounded repair.
    r = run(client, pid, run_id)
    assert r["counters"]["attempt"] == 2, r["outcome"].get("escalation")
    defects = client.get(f"/api/v1/projects/{pid}/defects?run_id={run_id}", headers=H("qa")).json()
    assert [(d["criterion_key"], d["status"]) for d in defects] == [("FR-001-AC2", "verified")]

    # 13. Release readiness report.
    assert r["status"] == "awaiting_approval" and r["current_stage"] == "release_staging_gate"
    rel = client.get(f"/api/v1/projects/{pid}/releases", headers=H("approver")).json()[0]
    report = rel["readiness"]
    assert report["eligible"] is True and report["blockers"] == []
    names = {c["name"]: c["status"] for c in report["checks"]}
    assert names["must-have criteria verified"] == "pass"
    assert names["regression suite passed on release commit"] == "pass"
    assert names["other criteria verifiable"] == "warn"  # NFR-001 has no automated test: shown, not hidden
    assert rel["commit"] == r["outcome"]["head_commit"] and len(rel["artifact_sha256"]) == 64

    # 14. Explicit approval before staging; only a release approver may give it.
    req = pending(client, pid, "approver")[0]
    assert req["gate"] == "release_staging" and req["artifact_id"] == rel["id"]
    assert decide(client, pid, req["id"], "approved", "owner").status_code == 403
    assert decide(client, pid, req["id"], "approved", "approver", "Ship to staging").status_code == 200
    drain()

    # 15. Deployment, post-deployment verification and recorded evidence.
    r = run(client, pid, run_id)
    assert r["status"] == "completed", r["outcome"].get("escalation")
    assert "disabled by policy" in r["outcome"]["production"]
    rel = client.get(f"/api/v1/projects/{pid}/releases/{rel['id']}", headers=H("auditor")).json()
    assert rel["status"] == "deployed_staging"
    dep = rel["deployments"][0]
    assert (
        dep["environment"] == "staging" and dep["status"] == "succeeded" and dep["adapter"] == "local_staging"
    )
    assert dep["smoke"]["integrity"]["ok"] is True
    assert all(c["ok"] for c in dep["smoke"]["checks"]) and dep["smoke"]["checks"]
    assert dep["health"]["checks"][0]["output"].strip().endswith("1.0.0")
    assert dep["health"]["verdict"]["recommendation"] == "keep"
    deployed = get_settings().staging_dir / pid / "current" / "task_service" / "service.py"
    assert "len(normalized) > MAX_TITLE_LENGTH" in deployed.read_text()  # the repaired code is what shipped

    assert (
        client.post(f"/api/v1/projects/{pid}/releases/{rel['id']}/promote", headers=H("approver")).status_code
        == 403
    )

    trace = client.get(f"/api/v1/projects/{pid}/traceability?run_id={run_id}", headers=H("auditor")).json()
    fr1 = next(x for x in trace["rows"] if x["criterion"] == "FR-001-AC2")
    assert fr1["release"] == [{"version": rel["version"], "status": "deployed_staging"}]
    audit = client.get(f"/api/v1/projects/{pid}/audit?limit=500", headers=H("auditor")).json()["items"]
    actions = {e["action"] for e in audit}
    assert {
        "document.uploaded",
        "run.started",
        "approval.approved",
        "code.applied",
        "release.created",
        "release.deployed",
        "run.completed",
    } <= actions
    agents = {a["agent"] for a in r["agent_runs"]}
    assert agents == {"planner", "architect", "developer", "qa", "ba-verifier", "shipping"}
    assert {a["strategy"] for a in r["agent_runs"] if a["agent"] == "developer"} == {"scripted"}


def test_release_blocked_when_a_must_criterion_cannot_be_verified(client: TestClient, project: dict) -> None:
    from tests.workflow.helpers import setup_project, start
    from tests.workflow.test_build_and_verify import approve_both_gates

    ctx = setup_project(client, project)
    pid = ctx["pid"]
    fr2 = next(
        r
        for r in client.get(f"/api/v1/projects/{pid}/requirements", headers=H("owner")).json()
        if r["key"] == "FR-002"
    )
    client.post(
        f"/api/v1/projects/{pid}/requirements/{fr2['id']}/criteria",
        json={"text": "Tabs inside the title are converted to spaces."},
        headers=H("owner"),
    )
    run_id = start(client, ctx)
    approve_both_gates(client, pid)
    r = run(client, pid, run_id)
    assert r["status"] == "escalated" and r["current_stage"] == "release_readiness"
    assert "must-have criteria verified" in r["outcome"]["escalation"]["reason"]
    rel = client.get(f"/api/v1/projects/{pid}/releases", headers=H("owner")).json()[0]
    assert rel["status"] == "blocked" and rel["deployments"] == []
    assert pending(client, pid, "approver") == []  # no approval is even requested for a blocked release


def test_failed_smoke_check_rolls_back(client: TestClient, project: dict, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from agentic_sdlc.releases import service as releases
    from tests.workflow.helpers import setup_project, start
    from tests.workflow.test_build_and_verify import approve_both_gates

    real = releases._commands

    def broken(tree, section):  # type: ignore[no-untyped-def]
        cmds = real(tree, section)
        if section == "smoke":
            cmds.append(
                {
                    "name": "always-fails",
                    "command": [get_settings().sandbox_python, "-c", "raise SystemExit(3)"],
                }
            )
        return cmds

    monkeypatch.setattr(releases, "_commands", broken)
    ctx = setup_project(client, project)
    pid = ctx["pid"]
    run_id = start(client, ctx)
    approve_both_gates(client, pid)
    req = pending(client, pid, "approver")[0]
    decide(client, pid, req["id"], "approved", "approver")
    drain()
    r = run(client, pid, run_id)
    assert r["status"] == "escalated" and r["current_stage"] == "post_deploy_verification"
    assert "rolled back" in r["outcome"]["escalation"]["reason"]
    rel = client.get(f"/api/v1/projects/{pid}/releases", headers=H("owner")).json()[0]
    assert rel["status"] == "rolled_back"
    dep = rel["deployments"][0]
    assert dep["status"] == "rolled_back" and dep["rollback"]["performed"] is True
    assert not (get_settings().staging_dir / pid / "current").exists()  # no previous release to restore
