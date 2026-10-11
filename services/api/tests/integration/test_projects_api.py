from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from agentic_sdlc.core.db import session_scope
from agentic_sdlc.orm import AuditEvent
from tests.conftest import H


def test_health_and_info(client: TestClient) -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    info = client.get("/api/v1/system/info").json()
    assert info["app_env"] == "test"
    assert info["model_inference"] == "unavailable"
    assert info["production_deploy_enabled"] is False


def test_requires_authentication(client: TestClient) -> None:
    r = client.get("/api/v1/projects")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["correlation_id"]
    assert client.get("/api/v1/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_correlation_id_round_trip(client: TestClient) -> None:
    cid = "abcd1234-5678"
    r = client.get("/api/v1/health", headers={"X-Request-ID": cid})
    assert r.headers["X-Request-ID"] == cid


def test_create_and_retrieve_project(client: TestClient) -> None:
    r = client.post(
        "/api/v1/projects", json={"name": "Order Portal", "goals": "faster orders"}, headers=H("owner")
    )
    assert r.status_code == 201
    p = r.json()
    assert p["key"].startswith("OP")
    assert p["version"] == 1
    got = client.get(f"/api/v1/projects/{p['id']}", headers=H("owner")).json()
    assert got["name"] == "Order Portal"
    listed = client.get("/api/v1/projects", headers=H("owner")).json()
    assert any(x["id"] == p["id"] for x in listed["items"])
    me = client.get("/api/v1/me", headers=H("owner")).json()
    assert me["project_roles"][p["id"]] == ["project_owner"]


def test_duplicate_key_conflict(client: TestClient) -> None:
    key = "K" + uuid.uuid4().hex[:5].upper()
    assert (
        client.post("/api/v1/projects", json={"name": "a", "key": key}, headers=H("owner")).status_code == 201
    )
    r = client.post("/api/v1/projects", json={"name": "b", "key": key}, headers=H("owner"))
    assert r.status_code == 409


def test_optimistic_concurrency_on_update(client: TestClient, project: dict) -> None:
    pid = project["id"]
    ok = client.patch(
        f"/api/v1/projects/{pid}", json={"expected_version": 1, "goals": "g1"}, headers=H("owner")
    )
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = client.patch(
        f"/api/v1/projects/{pid}", json={"expected_version": 1, "goals": "g2"}, headers=H("owner")
    )
    assert stale.status_code == 409


def test_cross_project_access_denied(client: TestClient, project: dict) -> None:
    # The engineer was not added to this other project; it must look nonexistent to them.
    other = client.post("/api/v1/projects", json={"name": "Private"}, headers=H("owner")).json()
    assert client.get(f"/api/v1/projects/{other['id']}", headers=H("engineer")).status_code == 404
    assert client.get(f"/api/v1/projects/{other['id']}/summary", headers=H("engineer")).status_code == 404
    listed = client.get("/api/v1/projects", headers=H("engineer")).json()["items"]
    assert other["id"] not in {x["id"] for x in listed}


def test_role_permissions_enforced(client: TestClient, project: dict) -> None:
    pid = project["id"]
    # reviewer can read but not update project or manage members
    assert client.get(f"/api/v1/projects/{pid}", headers=H("reviewer")).status_code == 200
    r = client.patch(
        f"/api/v1/projects/{pid}", json={"expected_version": 1, "goals": "x"}, headers=H("reviewer")
    )
    assert r.status_code == 403
    assert r.json()["error"]["details"]["permission"] == "project.update"
    r = client.post(
        f"/api/v1/projects/{pid}/members",
        json={"email": "admin@example.dev", "role": "engineer"},
        headers=H("engineer"),
    )
    assert r.status_code == 403
    # auditor can read the audit log, engineer cannot
    assert client.get(f"/api/v1/projects/{pid}/audit", headers=H("auditor")).status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/audit", headers=H("engineer")).status_code == 403


def test_admin_reads_but_cannot_edit(client: TestClient, project: dict) -> None:
    pid = project["id"]
    assert client.get(f"/api/v1/projects/{pid}", headers=H("admin")).status_code == 200
    r = client.patch(
        f"/api/v1/projects/{pid}", json={"expected_version": 1, "goals": "x"}, headers=H("admin")
    )
    assert r.status_code == 403


def test_cannot_remove_last_owner(client: TestClient, project: dict) -> None:
    pid = project["id"]
    me = client.get("/api/v1/me", headers=H("owner")).json()
    r = client.delete(f"/api/v1/projects/{pid}/members/{me['id']}/project_owner", headers=H("owner"))
    assert r.status_code == 409


def test_service_accounts_cannot_hold_roles(client: TestClient, project: dict) -> None:
    r = client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"email": "agent-architect@agents.agentic-sdlc.invalid", "role": "reviewer"},
        headers=H("owner"),
    )
    assert r.status_code == 422


def test_audit_trail_written_and_immutable(client: TestClient, project: dict) -> None:
    log = client.get(f"/api/v1/projects/{project['id']}/audit", headers=H("owner")).json()
    actions = {e["action"] for e in log["items"]}
    assert {"project.created", "membership.granted"} <= actions
    with session_scope() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.project_id == uuid.UUID(project["id"]))).first()
        assert ev is not None
        ev_id = ev.id
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"), session_scope() as db:
        db.execute(text("UPDATE audit_events SET action = 'tampered' WHERE id = :i"), {"i": ev_id})
    with pytest.raises(DBAPIError, match="append-only"), session_scope() as db:
        db.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": ev_id})


def test_events_endpoint_and_summary(client: TestClient, project: dict) -> None:
    evs = client.get(f"/api/v1/projects/{project['id']}/events", headers=H("owner")).json()
    assert evs[0]["type"] == "project.created"
    assert evs[0]["schema_version"] == 1
    later = client.get(
        f"/api/v1/projects/{project['id']}/events?after={evs[-1]['seq']}", headers=H("owner")
    ).json()
    assert later == []
    summary = client.get(f"/api/v1/projects/{project['id']}/summary", headers=H("engineer")).json()
    assert summary["my_roles"] == ["engineer"]
    assert summary["counts"]["requirements"] == 0


def test_validation_error_envelope(client: TestClient) -> None:
    r = client.post("/api/v1/projects", json={"name": ""}, headers=H("owner"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"
