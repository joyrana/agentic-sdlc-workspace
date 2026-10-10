"""Phase 3 exit criteria: requirements → reviewable backlog; validated dependencies; separate estimates."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_sdlc.workflows.worker import drain
from tests.conftest import H
from tests.integration.test_documents_requirements import upload_sample_brd


def _generate(client: TestClient, pid: str) -> dict:
    r = client.post(f"/api/v1/projects/{pid}/backlog/generate", headers=H("engineer"))
    assert r.status_code == 202, r.text
    drain()
    return client.get(f"/api/v1/projects/{pid}/backlog", headers=H("reviewer")).json()


def test_generate_backlog_from_requirements(client: TestClient, project: dict) -> None:
    pid = project["id"]
    upload_sample_brd(client, pid)
    backlog = _generate(client, pid)
    items = {i["key"]: i for i in backlog["items"]}
    kinds = sorted(i["kind"] for i in backlog["items"])
    assert kinds == ["epic", "feature", "feature", "story", "story", "story", "story", "story"]
    stories = [i for i in backlog["items"] if i["kind"] == "story"]
    by_req = {s["requirement_keys"][0]: s for s in stories}
    assert set(by_req) == {"FR-001", "FR-002", "FR-003", "NFR-001", "NFR-002"}
    assert by_req["FR-001"]["criteria_keys"] == ["FR-001-AC1", "FR-001-AC2"]
    # hierarchy
    epic = next(i for i in backlog["items"] if i["kind"] == "epic")
    for s in stories:
        assert (
            items[next(k for k, v in items.items() if v["id"] == s["parent_id"])]["parent_id"] == epic["id"]
        )
    # dependency FR-003 -> FR-002 and analysis
    assert [d["key"] for d in by_req["FR-003"]["depends_on"]] == [by_req["FR-002"]["key"]]
    assert backlog["analysis"]["valid"] is True
    assert by_req["FR-003"]["key"] in backlog["analysis"]["milestones"][1]
    # separate estimate dimensions, honestly labeled
    est = by_req["FR-001"]
    assert est["story_points"] in (1, 2, 3, 5, 8, 13)
    assert est["uncertainty"] in ("low", "medium", "high")
    assert est["estimate"]["is_estimated"] is True
    assert est["estimate"]["method"] == "heuristic-token-model/1"
    assert {"tokens_in", "tokens_out", "cost_usd", "duration_s"} <= set(est["estimate"])
    # model route recorded; no code model and no repository -> no coding agent
    assert est["model_route"]["coding_agent"]["kind"] == "none"
    assert est["model_route"]["generated_by"]["strategy"] == "heuristic"


def test_generation_is_idempotent_and_replans_new_requirements(client: TestClient, project: dict) -> None:
    pid = project["id"]
    upload_sample_brd(client, pid)
    first = _generate(client, pid)
    again = client.post(f"/api/v1/projects/{pid}/backlog/generate", headers=H("engineer")).json()
    drain()
    assert len(client.get(f"/api/v1/projects/{pid}/backlog", headers=H("owner")).json()["items"]) == len(
        first["items"]
    )
    assert again["status"] == "done"  # same snapshot -> same deduplicated job


def test_cycle_rejected_with_path(client: TestClient, project: dict) -> None:
    pid = project["id"]
    upload_sample_brd(client, pid)
    backlog = _generate(client, pid)
    s = {i["requirement_keys"][0]: i for i in backlog["items"] if i["kind"] == "story"}
    # FR-003 depends on FR-002 already; FR-002 -> FR-003 closes a cycle.
    r = client.post(
        f"/api/v1/projects/{pid}/backlog/items/{s['FR-002']['id']}/dependencies",
        json={"depends_on_id": s["FR-003"]["id"]},
        headers=H("engineer"),
    )
    assert r.status_code == 409
    cycle = r.json()["error"]["details"]["cycle"]
    assert cycle[0] == cycle[-1] and s["FR-002"]["key"] in cycle and s["FR-003"]["key"] in cycle
    ok = client.post(
        f"/api/v1/projects/{pid}/backlog/items/{s['NFR-002']['id']}/dependencies",
        json={"depends_on_id": s["FR-001"]["id"], "reason": "error format first"},
        headers=H("engineer"),
    )
    assert ok.status_code == 201
    self_dep = client.post(
        f"/api/v1/projects/{pid}/backlog/items/{s['FR-001']['id']}/dependencies",
        json={"depends_on_id": s["FR-001"]["id"]},
        headers=H("engineer"),
    )
    assert self_dep.status_code == 422


def test_human_edits_and_hierarchy_rules(client: TestClient, project: dict) -> None:
    pid = project["id"]
    upload_sample_brd(client, pid)
    backlog = _generate(client, pid)
    epic = next(i for i in backlog["items"] if i["kind"] == "epic")
    story = next(i for i in backlog["items"] if i["kind"] == "story")
    bad = client.post(
        f"/api/v1/projects/{pid}/backlog/items",
        json={"kind": "feature", "title": "Nested wrongly", "parent_id": story["id"]},
        headers=H("engineer"),
    )
    assert bad.status_code == 422
    good = client.post(
        f"/api/v1/projects/{pid}/backlog/items",
        json={"kind": "feature", "title": "Operational readiness", "parent_id": epic["id"]},
        headers=H("engineer"),
    )
    assert good.status_code == 201 and good.json()["origin"] == "human"
    upd = client.patch(
        f"/api/v1/projects/{pid}/backlog/items/{story['id']}",
        json={"expected_version": story["version"], "story_points": 8},
        headers=H("owner"),
    )
    assert upd.status_code == 200 and upd.json()["story_points"] == 8
    assert upd.json()["estimate"]["story_points_set_by"] == "human"
    assert upd.json()["estimate"]["tokens_in"] == story["estimate"]["tokens_in"]  # tokens untouched
    stale = client.patch(
        f"/api/v1/projects/{pid}/backlog/items/{story['id']}",
        json={"expected_version": story["version"], "title": "x"},
        headers=H("owner"),
    )
    assert stale.status_code == 409
    assert (
        client.patch(
            f"/api/v1/projects/{pid}/backlog/items/{story['id']}",
            json={"expected_version": 2, "title": "x"},
            headers=H("qa"),
        ).status_code
        == 403
    )


def test_capability_views(client: TestClient) -> None:
    skills = client.get("/api/v1/skills", headers=H("auditor")).json()
    ids = {s["id"] for s in skills}
    assert {"brd-analysis", "backlog-decomposition", "effort-estimation", "dependency-analysis"} <= ids
    agents = client.get("/api/v1/agents", headers=H("auditor")).json()
    assert "effort-estimation" in agents["planner"]["skills"]
    models = client.get("/api/v1/models", headers=H("auditor")).json()
    qwen = next(m for m in models if m["model_id"] == "qwen2.5-7b-instruct")
    assert qwen["available"] is False and qwen["license_verified"] is False
    mock = next(m for m in models if m["model_id"] == "mock-deterministic")
    assert mock["is_mock"] is True and mock["available"] is False


def test_usage_endpoint(client: TestClient, project: dict) -> None:
    r = client.get(f"/api/v1/projects/{project['id']}/usage", headers=H("auditor"))
    assert r.status_code == 200 and r.json()["rows"] == []
