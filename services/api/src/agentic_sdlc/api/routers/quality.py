"""Evidence: code changes, test runs, verification, defects, traceability."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from agentic_sdlc.api.deps import DB, project_permission
from agentic_sdlc.core.errors import NotFound
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    BacklogItem,
    CodeChange,
    Defect,
    DocumentVersion,
    Project,
    Release,
    Requirement,
    SourceDocument,
    TestRun,
    VerificationResult,
    backlog_item_criteria,
    backlog_item_requirements,
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


@router.get("/projects/{project_id}/verification")
def verification(project: Read, db: DB, run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(VerificationResult, AcceptanceCriterion, Requirement)
        .join(AcceptanceCriterion, AcceptanceCriterion.id == VerificationResult.acceptance_criterion_id)
        .join(Requirement, Requirement.id == AcceptanceCriterion.requirement_id)
        .where(VerificationResult.project_id == project.id, VerificationResult.run_id == run_id)
        .order_by(AcceptanceCriterion.key, VerificationResult.attempt)
    ).all()
    return [
        {
            "criterion_key": c.key,
            "criterion": c.text,
            "requirement_key": r.key,
            "priority": r.priority,
            "attempt": v.attempt,
            "status": v.status,
            "confidence": v.confidence,
            "gaps": v.gaps,
            "evidence": v.evidence,
        }
        for v, c, r in rows
    ]


@router.get("/projects/{project_id}/defects")
def defects(
    project: Read,
    db: DB,
    status: Annotated[Literal["open", "all"], Query()] = "all",
    run_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    stmt = select(Defect).where(Defect.project_id == project.id)
    if status == "open":
        stmt = stmt.where(Defect.status == "open")
    if run_id:
        stmt = stmt.where(Defect.run_id == run_id)
    out = []
    for d in db.scalars(stmt.order_by(Defect.created_at)):
        crit = db.get(AcceptanceCriterion, d.acceptance_criterion_id) if d.acceptance_criterion_id else None
        bug = db.get(BacklogItem, d.backlog_item_id) if d.backlog_item_id else None
        out.append(
            {
                "id": str(d.id),
                "run_id": str(d.run_id),
                "title": d.title,
                "severity": d.severity,
                "status": d.status,
                "criterion_key": crit.key if crit else None,
                "backlog_key": bug.key if bug else None,
                "reproduction_steps": d.reproduction_steps,
                "expected": d.expected,
                "actual": d.actual,
                "evidence": d.evidence,
                "found_in_attempt": d.found_in_attempt,
                "fixed_in_attempt": d.fixed_in_attempt,
                "created_at": d.created_at.isoformat(),
            }
        )
    return out


@router.get("/projects/{project_id}/traceability")
def traceability(project: Read, db: DB, run_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Source → requirement → criterion → backlog item → code change → test evidence → verdict → release.

    Built only from explicit foreign keys and link tables (never similarity).
    """
    rows = []
    reqs = db.scalars(
        select(Requirement).where(Requirement.project_id == project.id).order_by(Requirement.key)
    ).all()
    changes = db.scalars(
        select(CodeChange)
        .where(CodeChange.project_id == project.id, *([CodeChange.run_id == run_id] if run_id else []))
        .order_by(CodeChange.attempt)
    ).all()
    releases = db.scalars(
        select(Release).where(
            Release.project_id == project.id, *([Release.run_id == run_id] if run_id else [])
        )
    ).all()
    for r in reqs:
        source = None
        if r.document_version_id:
            dv = db.get(DocumentVersion, r.document_version_id)
            doc = db.get(SourceDocument, dv.document_id) if dv else None
            source = {
                "document": doc.title if doc else None,
                "version": dv.version if dv else None,
                "block_id": r.locator.get("block_id"),
                "section": r.locator.get("section_path"),
            }
        items = db.scalars(
            select(BacklogItem)
            .join(backlog_item_requirements, backlog_item_requirements.c.backlog_item_id == BacklogItem.id)
            .where(backlog_item_requirements.c.requirement_id == r.id)
        ).all()
        for c in db.scalars(
            select(AcceptanceCriterion)
            .where(AcceptanceCriterion.requirement_id == r.id)
            .order_by(AcceptanceCriterion.key)
        ):
            crit_items = db.scalars(
                select(BacklogItem.key)
                .join(backlog_item_criteria, backlog_item_criteria.c.backlog_item_id == BacklogItem.id)
                .where(backlog_item_criteria.c.acceptance_criterion_id == c.id)
            ).all()
            vstmt = select(VerificationResult).where(VerificationResult.acceptance_criterion_id == c.id)
            if run_id:
                vstmt = vstmt.where(VerificationResult.run_id == run_id)
            latest = db.scalar(vstmt.order_by(VerificationResult.created_at.desc()).limit(1))
            defs = db.scalars(select(Defect).where(Defect.acceptance_criterion_id == c.id)).all()
            rows.append(
                {
                    "source": source,
                    "requirement": r.key,
                    "requirement_status": r.status,
                    "criterion": c.key,
                    "criterion_text": c.text,
                    "backlog_items": sorted(set(crit_items) | {i.key for i in items}),
                    "code_changes": [
                        {"attempt": ch.attempt, "commit": ch.head_commit}
                        for ch in changes
                        if latest and latest.run_id == ch.run_id
                    ],
                    "verification": None
                    if latest is None
                    else {
                        "status": latest.status,
                        "attempt": latest.attempt,
                        "tests": latest.evidence.get("tests", []),
                    },
                    "defects": [{"title": d.title, "status": d.status} for d in defs],
                    "release": [
                        {"version": rel.version_label, "status": rel.status}
                        for rel in releases
                        if latest and rel.run_id == latest.run_id
                    ],
                }
            )
    return {"rows": rows}
