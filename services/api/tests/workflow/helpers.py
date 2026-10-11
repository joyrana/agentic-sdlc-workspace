from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from agentic_sdlc.workflows.worker import drain
from tests.conftest import H
from tests.helpers import SAMPLE_TRANSCRIPT
from tests.integration.test_documents_requirements import upload, upload_sample_brd


def setup_project(
    client: TestClient, project: dict, *, transcript: bool = False, limits: dict[str, Any] | None = None
) -> dict[str, Any]:
    pid = project["id"]
    upload_sample_brd(client, pid)
    if transcript:
        upload(client, pid, "kickoff.txt", SAMPLE_TRANSCRIPT.read_bytes(), kind="transcript")
        drain()
    repo = client.post(
        f"/api/v1/projects/{pid}/repositories",
        json={"name": "sample-task-service", "kind": "fixture", "location": "sample-task-service"},
        headers=H("owner"),
    )
    assert repo.status_code == 201, repo.text
    if limits:
        p = client.get(f"/api/v1/projects/{pid}", headers=H("owner")).json()
        r = client.patch(
            f"/api/v1/projects/{pid}",
            json={"expected_version": p["version"], "limits": limits},
            headers=H("owner"),
        )
        assert r.status_code == 200, r.text
    return {"pid": pid, "repo_id": repo.json()["id"]}


def start(client: TestClient, ctx: dict[str, Any]) -> str:
    r = client.post(
        f"/api/v1/projects/{ctx['pid']}/runs", json={"repository_id": ctx["repo_id"]}, headers=H("engineer")
    )
    assert r.status_code == 202, r.text
    drain()
    return r.json()["id"]


def run(client: TestClient, pid: str, run_id: str) -> dict[str, Any]:
    r = client.get(f"/api/v1/projects/{pid}/runs/{run_id}", headers=H("owner"))
    assert r.status_code == 200, r.text
    return r.json()


def pending(client: TestClient, pid: str, who: str = "owner") -> list[dict[str, Any]]:
    return client.get(f"/api/v1/projects/{pid}/approvals", headers=H(who)).json()


def decide(client: TestClient, pid: str, request_id: str, decision: str, who: str, comment: str = "") -> Any:
    return client.post(
        f"/api/v1/projects/{pid}/approvals/{request_id}/decision",
        json={"decision": decision, "comment": comment},
        headers=H(who),
    )
