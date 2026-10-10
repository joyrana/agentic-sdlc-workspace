"""Phase 4 exit criteria: architecture review/revision, authenticated auditable approvals,
stale-approval invalidation, durable resume after restart."""

from __future__ import annotations

import os
import subprocess
import sys

from fastapi.testclient import TestClient

from agentic_sdlc.workflows.worker import drain
from tests.conftest import API_DIR, H
from tests.workflow.helpers import decide, pending, run, setup_project, start


def _to_architecture_gate(client: TestClient, project: dict, **kw) -> tuple[str, str, dict]:  # type: ignore[no-untyped-def]
    ctx = setup_project(client, project, **kw)
    run_id = start(client, ctx)
    r = run(client, ctx["pid"], run_id)
    assert r["status"] == "awaiting_approval", r
    assert r["current_stage"] == "architecture_gate"
    return ctx["pid"], run_id, ctx


def test_full_gated_flow_with_architecture_and_scope_approval(client: TestClient, project: dict) -> None:
    pid, run_id, _ = _to_architecture_gate(client, project)
    r = run(client, pid, run_id)
    # Inputs snapshotted, backlog planned, repository analyzed, proposal + ADR produced.
    assert set(r["input_artifacts"]["requirements"]) == {"FR-001", "FR-002", "FR-003", "NFR-001", "NFR-002"}
    assert len(r["outcome"]["stories"]) == 5
    repo = r["outcome"]["repository"]
    assert repo["frameworks"] == ["pytest"] and repo["deploy_descriptor"] is True
    assert {a["skill_id"] for a in r["agent_runs"]} >= {
        "backlog-decomposition",
        "repository-exploration",
        "architecture-design",
        "adr-generation",
    }
    assert all(a["strategy"] in ("heuristic",) for a in r["agent_runs"])
    assert {t["tool"] for t in r["tool_invocations"]} == {"repo.read"}

    proposal = client.get(f"/api/v1/projects/{pid}/architecture", headers=H("reviewer")).json()[0]
    content = proposal["content"]
    assert content["recommended_option"] == "Validate at the service entry point"
    assert "TaskService.create_task" in content["components"][0]["name"]
    assert {t["requirement_key"] for t in content["traceability"]} == {
        "FR-001",
        "FR-002",
        "FR-003",
        "NFR-001",
        "NFR-002",
    }
    assert content["diagram_mermaid"].startswith("flowchart")
    adrs = client.get(f"/api/v1/projects/{pid}/adrs", headers=H("reviewer")).json()
    assert adrs[0]["number"] == 1 and adrs[0]["status"] == "proposed"

    req = pending(client, pid, "reviewer")[0]
    assert req["gate"] == "architecture" and req["can_decide"] is True
    assert req["content_hash"] == proposal["content_hash"]
    # Wrong roles cannot decide.
    approver_view = pending(client, pid, "approver")[0]
    assert approver_view["can_decide"] is False and "reviewer" in approver_view["cannot_decide_reason"]
    assert decide(client, pid, req["id"], "approved", "approver").status_code == 403
    assert decide(client, pid, req["id"], "approved", "engineer").status_code == 403
    assert decide(client, pid, req["id"], "approved", "admin").status_code == 403

    ok = decide(client, pid, req["id"], "approved", "reviewer", "Looks right")
    assert ok.status_code == 200 and ok.json()["decision"]["decision"] == "approved"
    assert decide(client, pid, req["id"], "approved", "owner").status_code == 409  # decided once
    drain()

    r = run(client, pid, run_id)
    assert r["status"] == "awaiting_approval" and r["current_stage"] == "implementation_scope_gate"
    scope_req = pending(client, pid)[0]
    assert scope_req["gate"] == "implementation_scope"
    assert decide(client, pid, scope_req["id"], "approved", "reviewer").status_code == 403  # owner only
    assert decide(client, pid, scope_req["id"], "approved", "owner").status_code == 200
    drain()
    r = run(client, pid, run_id)
    assert r["outcome"]["approved_scope"] and len(r["outcome"]["approved_scope"]) == 5
    assert r["status"] != "awaiting_approval" or r["current_stage"] != "implementation_scope_gate"

    adrs = client.get(f"/api/v1/projects/{pid}/adrs", headers=H("reviewer")).json()
    assert adrs[0]["status"] == "accepted"
    audit = client.get(f"/api/v1/projects/{pid}/audit?action=approval.approved", headers=H("auditor")).json()
    assert audit["total"] == 2
    details = audit["items"][-1]["details"]
    assert details["gate"] == "architecture" and details["content_hash"] == proposal["content_hash"]


def test_changes_requested_produces_new_revision(client: TestClient, project: dict) -> None:
    pid, run_id, _ = _to_architecture_gate(client, project)
    req = pending(client, pid, "reviewer")[0]
    assert (
        decide(client, pid, req["id"], "changes_requested", "reviewer").status_code == 422
    )  # comment required
    r = decide(client, pid, req["id"], "changes_requested", "reviewer", "Use the dedicated validation module")
    assert r.status_code == 200
    drain()
    proposal = client.get(f"/api/v1/projects/{pid}/architecture", headers=H("reviewer")).json()[0]
    assert proposal["version"] == 2
    assert proposal["content"]["recommended_option"] == "Dedicated validation module"
    assert proposal["content"]["review_feedback"] == ["Use the dedicated validation module"]
    new_req = pending(client, pid, "reviewer")
    assert len(new_req) == 1 and new_req[0]["artifact_version"] == 2 and new_req[0]["id"] != req["id"]
    detail = client.get(f"/api/v1/projects/{pid}/architecture/{proposal['id']}", headers=H("reviewer")).json()
    assert [rev["version"] for rev in detail["revisions"]] == [1, 2]
    assert run(client, pid, run_id)["counters"]["architecture_revisions"] == 1


def test_edit_makes_pending_approval_stale_and_enforces_separation_of_duties(
    client: TestClient, project: dict
) -> None:
    pid, _, _ = _to_architecture_gate(client, project)
    old = pending(client, pid, "reviewer")[0]
    proposal = client.get(f"/api/v1/projects/{pid}/architecture", headers=H("owner")).json()[0]
    content = dict(proposal["content"])
    content["decision_rationale"] += " Edited by the engineer to add context."
    edited = client.patch(
        f"/api/v1/projects/{pid}/architecture/{proposal['id']}",
        json={"expected_version": proposal["version"], "content": content},
        headers=H("engineer"),
    )
    assert edited.status_code == 200 and edited.json()["version"] == 2

    stale = decide(client, pid, old["id"], "approved", "reviewer")
    assert stale.status_code == 409
    drain()  # the gate wakes up and requests approval for version 2
    fresh = pending(client, pid, "reviewer")
    assert len(fresh) == 1 and fresh[0]["artifact_version"] == 2

    # Invalid edits are rejected by the skill's own output schema.
    bad = client.patch(
        f"/api/v1/projects/{pid}/architecture/{proposal['id']}",
        json={"expected_version": 2, "content": {"title": "x"}},
        headers=H("engineer"),
    )
    assert bad.status_code == 422

    # The owner edits again; the owner then cannot approve their own revision.
    content["decision_rationale"] += " Owner tweak."
    client.patch(
        f"/api/v1/projects/{pid}/architecture/{proposal['id']}",
        json={"expected_version": 2, "content": content},
        headers=H("owner"),
    )
    drain()
    req = pending(client, pid, "owner")[0]
    assert req["artifact_version"] == 3
    assert req["can_decide"] is False and req["cannot_decide_reason"] == "you authored this revision"
    assert decide(client, pid, req["id"], "approved", "owner").status_code == 403
    assert decide(client, pid, req["id"], "approved", "reviewer").status_code == 200


def test_rejection_cancels_run(client: TestClient, project: dict) -> None:
    pid, run_id, _ = _to_architecture_gate(client, project)
    req = pending(client, pid, "reviewer")[0]
    assert (
        decide(client, pid, req["id"], "rejected", "reviewer", "Out of scope this quarter").status_code == 200
    )
    drain()
    r = run(client, pid, run_id)
    assert r["status"] == "cancelled" and r["outcome"]["rejected"]["gate"] == "architecture"


def test_clarification_gate_waits_for_blocking_questions(client: TestClient, project: dict) -> None:
    ctx = setup_project(client, project, transcript=True)
    run_id = start(client, ctx)
    r = run(client, ctx["pid"], run_id)
    assert r["status"] == "awaiting_approval" and r["current_stage"] == "clarification_gate"
    qs = client.get(f"/api/v1/projects/{ctx['pid']}/questions", headers=H("owner")).json()
    blocking = [q for q in qs if q["blocking"]]
    client.post(
        f"/api/v1/projects/{ctx['pid']}/questions/{blocking[0]['id']}/resolve",
        json={"resolution": "120 characters is correct."},
        headers=H("reviewer"),
    )
    drain()
    assert run(client, ctx["pid"], run_id)["current_stage"] == "architecture_gate"


def test_revision_limit_escalates(client: TestClient, project: dict) -> None:
    pid, run_id, _ = _to_architecture_gate(client, project, limits={"max_architecture_revisions": 0})
    req = pending(client, pid, "reviewer")[0]
    decide(client, pid, req["id"], "changes_requested", "reviewer", "Try again")
    drain()
    r = run(client, pid, run_id)
    assert r["status"] == "escalated"
    assert "revision limit" in r["outcome"]["escalation"]["reason"]


def test_budget_exhaustion_escalates(client: TestClient, project: dict) -> None:
    ctx = setup_project(client, project, limits={"max_wall_clock_s": 0})
    run_id = start(client, ctx)
    r = run(client, ctx["pid"], run_id)
    assert r["status"] == "escalated"
    assert "wall-clock budget" in r["outcome"]["escalation"]["reason"]


def test_cancel_while_waiting_and_single_active_run(client: TestClient, project: dict) -> None:
    pid, run_id, ctx = _to_architecture_gate(client, project)
    dup = client.post(
        f"/api/v1/projects/{pid}/runs", json={"repository_id": ctx["repo_id"]}, headers=H("engineer")
    )
    assert dup.status_code == 409
    req = pending(client, pid, "reviewer")[0]
    assert (
        client.post(
            f"/api/v1/projects/{pid}/runs/{run_id}/cancel", json={"reason": "x"}, headers=H("engineer")
        ).status_code
        == 403
    )
    c = client.post(
        f"/api/v1/projects/{pid}/runs/{run_id}/cancel",
        json={"reason": "Priorities changed"},
        headers=H("owner"),
    )
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert decide(client, pid, req["id"], "approved", "reviewer").status_code == 409
    drain()
    assert run(client, pid, run_id)["status"] == "cancelled"


def test_resume_after_restart_in_a_separate_process(client: TestClient, project: dict) -> None:
    """The decision is processed by a fresh worker process: state comes only from Postgres checkpoints."""
    pid, run_id, _ = _to_architecture_gate(client, project)
    req = pending(client, pid, "reviewer")[0]
    decide(client, pid, req["id"], "approved", "reviewer")
    env = {**os.environ, "PYTHONPATH": str(API_DIR / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "agentic_sdlc.cli", "worker", "--once"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    r = run(client, pid, run_id)
    assert r["current_stage"] == "implementation_scope_gate" and r["status"] == "awaiting_approval"
    # Earlier stages were not re-executed by the new process.
    designs = [a for a in r["agent_runs"] if a["skill_id"] == "architecture-design"]
    assert len(designs) == 1


def test_spurious_resume_does_not_advance(client: TestClient, project: dict) -> None:
    from agentic_sdlc.core.db import session_scope
    from agentic_sdlc.workflows import jobs

    pid, run_id, _ = _to_architecture_gate(client, project)
    with session_scope() as db:
        import uuid

        jobs.enqueue(
            db,
            kind="delivery.resume",
            dedup_key=f"spurious:{run_id}",
            run_id=uuid.UUID(run_id),
            payload={"run_id": run_id, "reason": "spurious"},
        )
    drain()
    r = run(client, pid, run_id)
    assert r["status"] == "awaiting_approval" and r["current_stage"] == "architecture_gate"
    assert len(pending(client, pid, "reviewer")) == 1
