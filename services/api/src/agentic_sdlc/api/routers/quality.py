"""Evidence: code changes and developer test runs."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from agentic_sdlc.api.deps import DB, project_permission
from agentic_sdlc.core.errors import NotFound
from agentic_sdlc.orm import (
    CodeChange,
    Project,
    TestRun,
)

router = APIRouter(tags=["quality"])
Read = Annotated[Project, Depends(project_permission("quality.read"))]


def _change(c: CodeChange, with_diff: bool) -> dict[str, Any]:
    out = {
        "id": str(c.id),
        "run_id": str(c.run_id),
        "attempt": c.attempt,
        "branch": c.branch,
        "base_commit": c.base_commit,
        "head_commit": c.head_commit,
        "files": c.files,
        "summary": c.summary,
        "coding_agent": c.coding_agent,
        "status": c.status,
        "review": c.review,
        "created_at": c.created_at.isoformat(),
    }
    if with_diff:
        out["diff"] = c.diff
    return out


def _test_run(t: TestRun, with_log: bool) -> dict[str, Any]:
    out = {
        "id": str(t.id),
        "run_id": str(t.run_id),
        "suite": t.suite,
        "attempt": t.attempt,
        "status": t.status,
        "totals": t.totals,
        "cases": t.cases,
        "command": t.command,
        "sandbox": t.sandbox,
        "commit": t.commit,
        "code_change_id": str(t.code_change_id) if t.code_change_id else None,
        "created_at": t.created_at.isoformat(),
    }
    if with_log:
        out["log"] = t.log
    return out


@router.get("/projects/{project_id}/code-changes")
def code_changes(
    project: Read, db: DB, run_id: uuid.UUID | None = None, include_diff: bool = False
) -> list[dict[str, Any]]:
    stmt = select(CodeChange).where(CodeChange.project_id == project.id)
    if run_id:
        stmt = stmt.where(CodeChange.run_id == run_id)
    return [_change(c, include_diff) for c in db.scalars(stmt.order_by(CodeChange.created_at))]


@router.get("/projects/{project_id}/test-runs")
def test_runs(project: Read, db: DB, run_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    stmt = select(TestRun).where(TestRun.project_id == project.id)
    if run_id:
        stmt = stmt.where(TestRun.run_id == run_id)
    return [_test_run(t, False) for t in db.scalars(stmt.order_by(TestRun.created_at))]


@router.get("/projects/{project_id}/test-runs/{test_run_id}")
def test_run(test_run_id: uuid.UUID, project: Read, db: DB) -> dict[str, Any]:
    t = db.get(TestRun, test_run_id)
    if t is None or t.project_id != project.id:
        raise NotFound("test run not found")
    return _test_run(t, True)
