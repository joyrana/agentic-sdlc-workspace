"""Build stage: the Developer agent implements the approved scope in a disposable workspace.

Evidence rules:
- The patch is reviewed against policy and the approved plan before it touches any repository.
- The developer's own test run is recorded as evidence; it never decides acceptance (independent
  verification follows in a later stage).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.events import service as events
from agentic_sdlc.execution import workspace as wsmod
from agentic_sdlc.execution.coding_agents import load_scenario
from agentic_sdlc.execution.repo_reader import head_commit
from agentic_sdlc.execution.test_runner import run_suite
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    BacklogItem,
    CodeChange,
    Defect,
    ImplementationPlan,
    ProjectRepository,
    Requirement,
    TestRun,
    WorkflowRun,
    backlog_item_criteria,
)
from agentic_sdlc.skills.runtime import SkillRuntime
from agentic_sdlc.workflows.delivery import DeliveryState, agent_ctx, escalate_now, node, stage


def _repo(db: Session, run: WorkflowRun) -> ProjectRepository:
    repo = db.get(ProjectRepository, uuid.UUID(run.config["repository_id"]))
    assert repo is not None
    return repo


def _in_scope_items(db: Session, run: WorkflowRun) -> list[BacklogItem]:
    keys = run.outcome.get("approved_scope") or []
    return list(
        db.scalars(
            select(BacklogItem)
            .where(BacklogItem.project_id == run.project_id, BacklogItem.key.in_(keys))
            .order_by(BacklogItem.key)
        )
    )


def _root(db: Session, item: BacklogItem) -> BacklogItem:
    while item.parent_id is not None:
        parent = db.get(BacklogItem, item.parent_id)
        if parent is None:
            break
        item = parent
    return item


def scope_criteria(db: Session, run: WorkflowRun) -> list[dict[str, Any]]:
    """Criteria delivered by the approved in-scope items, with priority and owning item."""
    out: dict[str, dict[str, Any]] = {}
    for item in _in_scope_items(db, run):
        rows = db.execute(
            select(AcceptanceCriterion, Requirement)
            .join(
                backlog_item_criteria,
                backlog_item_criteria.c.acceptance_criterion_id == AcceptanceCriterion.id,
            )
            .join(Requirement, Requirement.id == AcceptanceCriterion.requirement_id)
            .where(
                backlog_item_criteria.c.backlog_item_id == item.id, AcceptanceCriterion.status != "rejected"
            )
        ).all()
        for crit, req in rows:
            out[crit.key] = {
                "id": str(crit.id),
                "key": crit.key,
                "text": crit.text,
                "priority": req.priority,
                "requirement_key": req.key,
                "requirement_id": str(req.id),
                "item_id": str(item.id),
                "origin": crit.origin,
            }
    return sorted(out.values(), key=lambda c: c["key"])


def _attempt(run: WorkflowRun) -> int:
    return int(run.counters.get("attempt", 1))


def _change_for(db: Session, run: WorkflowRun, attempt: int) -> CodeChange | None:
    return db.scalar(select(CodeChange).where(CodeChange.run_id == run.id, CodeChange.attempt == attempt))


def _test_run(db: Session, run: WorkflowRun, suite: str, attempt: int) -> TestRun | None:
    return db.scalar(
        select(TestRun).where(TestRun.run_id == run.id, TestRun.suite == suite, TestRun.attempt == attempt)
    )


def _save_test_run(
    db: Session, run: WorkflowRun, suite: str, attempt: int, result: dict[str, Any], change: CodeChange | None
) -> TestRun:
    tr = TestRun(
        project_id=run.project_id,
        run_id=run.id,
        code_change_id=change.id if change else None,
        suite=suite,
        attempt=attempt,
        status=result["status"],
        totals=result["totals"],
        cases=result["cases"],
        log=result["log"],
        command=result["command"],
        sandbox={
            **result["sandbox"],
            "tampered_files": result.get("tampered_files", []),
            "problems": result.get("problems", []),
        },
        commit=result["commit"],
    )
    db.add(tr)
    db.flush()
    events.emit(
        db,
        type="tests.completed",
        project_id=run.project_id,
        run_id=run.id,
        payload={"suite": suite, "attempt": attempt, "status": tr.status, "totals": tr.totals},
        artifact_refs=[{"type": "test_run", "id": str(tr.id)}],
    )
    return tr


@node("implement")
def implement(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "implement") as (db, run, budget):
        attempt = _attempt(run)
        if _change_for(db, run, attempt) is not None:
            return {"outcome": "ok"}  # idempotent re-entry after a crash
        repo = _repo(db, run)
        origin = Path(repo.location)
        plan = db.get(ImplementationPlan, uuid.UUID(run.outcome["plan_id"]))
        assert plan is not None
        if "base_commit" not in run.outcome:
            run.outcome = {**run.outcome, "base_commit": head_commit(origin, repo.default_branch)}
        base = run.outcome.get("head_commit") or run.outcome["base_commit"]
        items = _in_scope_items(db, run)
        root = _root(db, items[0])
        criteria = scope_criteria(db, run)
        open_defects = db.scalars(
            select(Defect).where(Defect.run_id == run.id, Defect.status == "open")
        ).all()
        result = SkillRuntime().execute(
            "code-generation",
            {
                "repository_id": str(repo.id),
                "base_commit": base,
                "attempt": attempt,
                "task": {
                    "title": plan.content["approach"],
                    "items": [i.key for i in items],
                    "approach": plan.content["approach"],
                    "criteria": [{"key": c["key"], "text": c["text"]} for c in criteria],
                },
                "plan_files": plan.content["files"],
                "defects": [
                    {"title": d.title, "expected": d.expected, "actual": d.actual} for d in open_defects
                ],
            },
            agent_ctx(
                db,
                run,
                "developer",
                "implement",
                budget,
                backlog_item_id=root.id,
                allow_scripted=load_scenario(repo) is not None,
            ),
        )
        diff = result.output["diff"]
        review = wsmod.review_patch(diff, allowed_files=set(plan.content["files"]))
        agent_info = {
            "strategy": result.strategy,
            "model_ref": result.model_ref,
            "is_mock": result.is_mock,
            "agent_run_id": str(result.agent_run_id),
            "notes": result.output["notes"],
        }
        branch = f"agent/{str(run.id)[:8]}"
        change = CodeChange(
            project_id=run.project_id,
            run_id=run.id,
            backlog_item_id=root.id,
            repository_id=repo.id,
            attempt=attempt,
            branch=branch,
            base_commit=base,
            diff=diff,
            files=review.files,
            summary=result.output["summary"],
            coding_agent=agent_info,
            review=review.as_dict(),
            status="proposed",
        )
        db.add(change)
        db.flush()
        if not review.ok:
            change.status = "rejected"
            escalate_now(
                db, run, "implement", "patch rejected by policy review: " + "; ".join(review.violations)
            )
            return {"halt": True}
        ws = wsmod.open_workspace(origin, base)
        try:
            head = wsmod.apply_and_commit(
                ws,
                diff,
                f"{plan.content['approach']} (attempt {attempt})\n\nRun: {run.id}\nItems: "
                f"{', '.join(i.key for i in items)}\n"
                f"Produced by: developer agent, {result.strategy} strategy",
            )
            wsmod.push_branch(ws, branch)
        finally:
            ws.cleanup()
        change.head_commit = head
        change.status = "applied"
        for item in items:
            item.status = "in_progress"
        audit.record(
            db,
            action="code.applied",
            actor_id=None,
            actor_type="agent",
            project_id=run.project_id,
            run_id=run.id,
            target_type="code_change",
            target_id=change.id,
            details={
                "attempt": attempt,
                "branch": branch,
                "head": head,
                "files": review.files,
                "strategy": result.strategy,
            },
        )
        events.emit(
            db,
            type="code.applied",
            project_id=run.project_id,
            run_id=run.id,
            payload={"attempt": attempt, "branch": branch, "head": head[:12], "strategy": result.strategy},
            artifact_refs=[{"type": "code_change", "id": str(change.id)}],
        )
        dev = run_suite(origin, head)
        _save_test_run(db, run, "developer", attempt, dev.as_dict(), change)
        run.outcome = {**run.outcome, "head_commit": head, "branch": branch}
    return {"outcome": "ok"}
