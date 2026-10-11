"""Build and verification stages: implement → qa_tests → ba_verification → (create_defects → implement)* →
regression.

Evidence rules:
- The developer's own test run is recorded but never used to decide acceptance.
- Acceptance is decided by the BA verifier from the QA agent's independent acceptance run for the exact
  commit under review.
- Defects are created from recorded failures; the repair loop is bounded by `max_repair_attempts`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.backlog.service import next_key as next_backlog_key
from agentic_sdlc.core.db import utcnow
from agentic_sdlc.events import service as events
from agentic_sdlc.execution import workspace as wsmod
from agentic_sdlc.execution.coding_agents import load_scenario
from agentic_sdlc.execution.repo_reader import head_commit
from agentic_sdlc.execution.test_runner import run_suite
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    AgentRun,
    BacklogItem,
    CodeChange,
    Defect,
    ImplementationPlan,
    ProjectRepository,
    Requirement,
    TestRun,
    TokenUsage,
    VerificationResult,
    WorkflowRun,
    backlog_item_criteria,
    backlog_item_requirements,
)
from agentic_sdlc.skills.runtime import SkillRuntime
from agentic_sdlc.workflows.delivery import DeliveryState, Escalate, agent_ctx, escalate_now, node, stage


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


@node("qa_tests")
def qa_tests(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "qa_tests") as (db, run, budget):
        attempt = _attempt(run)
        if _test_run(db, run, "acceptance", attempt) is None:
            change = _change_for(db, run, attempt)
            result = SkillRuntime().execute(
                "regression-testing",
                {
                    "repository_id": run.config["repository_id"],
                    "commit": run.outcome["head_commit"],
                    "suite": "acceptance",
                },
                agent_ctx(db, run, "qa", "qa_tests", budget),
            )
            _save_test_run(db, run, "acceptance", attempt, result.output, change)
    return {"outcome": "ok"}


@node("ba_verification")
def ba_verification(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "ba_verification") as (db, run, budget):
        attempt = _attempt(run)
        tr = _test_run(db, run, "acceptance", attempt)
        change = _change_for(db, run, attempt)
        assert tr is not None and change is not None
        criteria = scope_criteria(db, run)
        existing = db.scalars(
            select(VerificationResult).where(
                VerificationResult.run_id == run.id, VerificationResult.attempt == attempt
            )
        ).all()
        if not existing:
            result = SkillRuntime().execute(
                "acceptance-verification",
                {
                    "criteria": [{k: c[k] for k in ("key", "text", "priority")} for c in criteria],
                    "test_run_id": str(tr.id),
                    "code_change_id": str(change.id),
                },
                agent_ctx(db, run, "ba-verifier", "ba_verification", budget),
            )
            by_key = {c["key"]: c for c in criteria}
            for r in result.output["results"]:
                db.add(
                    VerificationResult(
                        project_id=run.project_id,
                        run_id=run.id,
                        acceptance_criterion_id=uuid.UUID(by_key[r["key"]]["id"]),
                        attempt=attempt,
                        status=r["status"],
                        confidence=r["confidence"],
                        evidence=r["evidence"],
                        gaps=r["gaps"],
                    )
                )
            db.flush()
            summary = result.output["summary"]
            events.emit(
                db,
                type="verification.completed",
                project_id=run.project_id,
                run_id=run.id,
                payload={"attempt": attempt, **summary},
            )
        verdicts = db.scalars(
            select(VerificationResult).where(
                VerificationResult.run_id == run.id, VerificationResult.attempt == attempt
            )
        ).all()
        unmapped = [c for c in tr.cases if c["outcome"] != "passed" and not c.get("criteria")]
        failed = any(v.status == "failed" for v in verdicts) or bool(unmapped) or tr.status == "error"
        run.outcome = {
            **run.outcome,
            "verification": {
                "attempt": attempt,
                **{
                    k: sum(1 for v in verdicts if v.status == k)
                    for k in ("verified", "failed", "unverifiable")
                },
            },
        }
    return {"outcome": "defects" if failed else "verified"}


@node("create_defects")
def create_defects(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "create_defects") as (db, run, budget):
        attempt = _attempt(run)
        tr = _test_run(db, run, "acceptance", attempt)
        change = _change_for(db, run, attempt)
        assert tr is not None and change is not None
        criteria = {c["key"]: c for c in scope_criteria(db, run)}
        verdicts = db.execute(
            select(VerificationResult, AcceptanceCriterion)
            .join(AcceptanceCriterion, AcceptanceCriterion.id == VerificationResult.acceptance_criterion_id)
            .where(
                VerificationResult.run_id == run.id,
                VerificationResult.attempt == attempt,
                VerificationResult.status == "failed",
            )
        ).all()
        failed = [
            {
                "key": c.key,
                "text": c.text,
                "priority": criteria.get(c.key, {}).get("priority", "should"),
                "requirement_key": criteria.get(c.key, {}).get("requirement_key"),
                "gaps": v.gaps,
                "evidence": v.evidence,
            }
            for v, c in verdicts
        ]
        unmapped = [c for c in tr.cases if c["outcome"] != "passed" and not c.get("criteria")]
        triage = SkillRuntime().execute(
            "bug-triage",
            {
                "failed": failed,
                "unmapped_failures": unmapped,
                "commit": tr.commit or "",
                "command": tr.command,
                "attempt": attempt,
            },
            agent_ctx(db, run, "qa", "create_defects", budget),
        )
        created = []
        for spec in triage.output["defects"]:
            if db.scalar(
                select(Defect).where(Defect.run_id == run.id, Defect.dedup_key == spec["dedup_key"])
            ):
                continue
            crit = criteria.get(spec.get("criterion_key") or "")
            parent_id = uuid.UUID(crit["item_id"]) if crit else change.backlog_item_id
            parent = db.get(BacklogItem, parent_id)
            assert parent is not None
            bug = BacklogItem(
                project_id=run.project_id,
                run_id=run.id,
                parent_id=parent.id,
                key=next(next_backlog_key(db, _project(db, run))),
                kind="bug",
                title=spec["title"][:300],
                description=spec["actual"][:4000],
                priority=1 if spec["severity"] != "minor" else 2,
                labels=["defect", spec["severity"]],
                origin="generated",
                assigned_agent="developer",
            )
            db.add(bug)
            db.flush()
            if crit:
                db.execute(
                    insert(backlog_item_requirements).values(
                        backlog_item_id=bug.id, requirement_id=uuid.UUID(crit["requirement_id"])
                    )
                )
                db.execute(
                    insert(backlog_item_criteria).values(
                        backlog_item_id=bug.id, acceptance_criterion_id=uuid.UUID(crit["id"])
                    )
                )
            defect = Defect(
                project_id=run.project_id,
                run_id=run.id,
                dedup_key=spec["dedup_key"],
                backlog_item_id=bug.id,
                source_item_id=parent.id,
                requirement_id=uuid.UUID(crit["requirement_id"]) if crit else None,
                acceptance_criterion_id=uuid.UUID(crit["id"]) if crit else None,
                code_change_id=change.id,
                test_run_id=tr.id,
                severity=spec["severity"],
                title=spec["title"][:300],
                reproduction_steps=spec["reproduction_steps"],
                expected=spec["expected"],
                actual=spec["actual"],
                evidence=spec["evidence"],
                status="open",
                found_in_attempt=attempt,
            )
            db.add(defect)
            db.flush()
            created.append(defect)
            events.emit(
                db,
                type="defect.created",
                project_id=run.project_id,
                run_id=run.id,
                payload={"title": defect.title, "severity": defect.severity, "attempt": attempt},
                artifact_refs=[{"type": "defect", "id": str(defect.id)}],
            )
        repairs_used = attempt - 1
        limit = int(run.config["limits"]["max_repair_attempts"])
        if repairs_used >= limit:
            escalate_now(
                db,
                run,
                "create_defects",
                f"repair limit reached ({limit} repair attempt(s)); {len(created)} defect(s) remain open",
            )
            return {"halt": True}
        run.counters = {**run.counters, "attempt": attempt + 1, "repairs": repairs_used + 1}
    return {"outcome": "repair"}


def _project(db: Session, run: WorkflowRun) -> Any:
    from agentic_sdlc.orm import Project

    return db.get(Project, run.project_id)


@node("regression")
def regression(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "regression") as (db, run, budget):
        attempt = _attempt(run)
        if _test_run(db, run, "regression", attempt) is None:
            result = SkillRuntime().execute(
                "regression-testing",
                {
                    "repository_id": run.config["repository_id"],
                    "commit": run.outcome["head_commit"],
                    "suite": "regression",
                },
                agent_ctx(db, run, "qa", "regression", budget),
            )
            tr = _save_test_run(db, run, "regression", attempt, result.output, _change_for(db, run, attempt))
            if tr.status != "passed":
                raise Escalate(
                    "the independent regression run did not reproduce the passing QA result "
                    f"({tr.status}); tests may be flaky or environment-dependent"
                )
        for d in db.scalars(
            select(Defect).where(Defect.run_id == run.id, Defect.status.in_(["open", "fixed"]))
        ):
            d.status = "verified"
            d.fixed_in_attempt = d.fixed_in_attempt or attempt
            bug = db.get(BacklogItem, d.backlog_item_id) if d.backlog_item_id else None
            if bug is not None:
                bug.status = "done"
        # Actuals: measured, apportioned evenly across delivered items (shared change set).
        items = _in_scope_items(db, run)
        tokens = db.execute(
            select(TokenUsage.input_tokens + TokenUsage.output_tokens, TokenUsage.is_estimated)
            .join(AgentRun, AgentRun.id == TokenUsage.agent_run_id)
            .where(TokenUsage.run_id == run.id, AgentRun.stage.in_(["implement", "qa_tests", "regression"]))
        ).all()
        total = sum(int(t) for t, _ in tokens)
        estimated = any(e for _, e in tokens)
        dev_runs = db.scalars(
            select(AgentRun).where(
                AgentRun.run_id == run.id, AgentRun.stage.in_(["implement", "qa_tests", "regression"])
            )
        ).all()
        duration = sum((a.duration_ms or 0) for a in dev_runs) / 1000
        strategy = sorted({a.strategy for a in dev_runs if a.agent == "developer"})
        for item in items:
            item.status = "done"
            item.actuals = {
                "attempts": attempt,
                "tokens_total": total // max(1, len(items)),
                "tokens_estimated": estimated if total else False,
                "apportioned": True,
                "duration_s": round(duration / max(1, len(items)), 2),
                "coding_strategy": strategy,
                "measured_at": utcnow().isoformat(),
            }
        run.outcome = {**run.outcome, "delivered_items": [i.key for i in items]}
    return {"outcome": "ok"}
