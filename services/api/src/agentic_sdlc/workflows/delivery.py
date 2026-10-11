"""Delivery workflow (LangGraph, Postgres-checkpointed).

snapshot_inputs → clarification_gate → plan_backlog → analyze_repository → propose_architecture
→ architecture_gate → plan_implementation → implementation_gate → … (later stages) → finalize

Rules every node follows:
- Start with `stage()`: the run must be active; budgets are checked; the stage is recorded.
- Gates commit their approval request before `interrupt()`, and after resuming re-read the decision
  from the database. A resume with no valid decision loops the gate back to waiting.
- Failures that need a person (budget, policy, unavailable skill) escalate the run with a reason.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import Connection
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_sdlc.approvals import service as approvals
from agentic_sdlc.architecture import service as arch
from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.backlog.graph import topological_layers
from agentic_sdlc.backlog.service import generate_backlog, requirements_snapshot
from agentic_sdlc.core.db import session_scope, utcnow
from agentic_sdlc.core.errors import BudgetExceeded, Conflict, DomainError, NotFound, ValidationFailed
from agentic_sdlc.core.logging import log
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import (
    BacklogDependency,
    BacklogItem,
    OpenQuestion,
    Project,
    ProjectRepository,
    Requirement,
    TokenUsage,
    User,
    WorkflowJob,
    WorkflowRun,
    backlog_item_criteria,
    backlog_item_requirements,
)
from agentic_sdlc.orm.requirements import AcceptanceCriterion
from agentic_sdlc.seed import agent_email
from agentic_sdlc.skills.base import BudgetTracker, ExecutionContext, Limits, SkillError
from agentic_sdlc.skills.runtime import SkillRuntime
from agentic_sdlc.workflows import jobs
from agentic_sdlc.workflows.checkpoint import checkpointer

logger = logging.getLogger("agentic_sdlc.delivery")
DEFINITION_VERSION = 1
TERMINAL = {"completed", "escalated", "cancelled", "failed"}
ACTIVE = {"queued", "running", "awaiting_approval"}


class DeliveryState(TypedDict, total=False):
    run_id: str
    outcome: str
    halt: bool
    feedback: list[str]


class Escalate(Exception):
    """A condition only a person can resolve."""


class RunStopped(Exception):
    """The run was cancelled or already finished."""


# --- run lifecycle ------------------------------------------------------------------------------------


def run_limits(project: Project) -> dict[str, Any]:
    s = get_settings()
    defaults = {
        "max_repair_attempts": s.max_repair_attempts,
        "max_architecture_revisions": s.max_architecture_revisions,
        "max_model_calls": s.max_model_calls,
        "max_tokens": s.max_tokens,
        "max_cost_usd": s.max_cost_usd,
        "max_wall_clock_s": s.max_wall_clock_s,
    }
    return {**defaults, **{k: v for k, v in (project.limits or {}).items() if k in defaults}}


def start_run(
    db: Session, principal: Principal, project: Project, *, repository_id: uuid.UUID
) -> WorkflowRun:
    repo = db.get(ProjectRepository, repository_id)
    if repo is None or repo.project_id != project.id:
        raise NotFound("repository not found")
    if not db.scalar(
        select(func.count()).select_from(Requirement).where(Requirement.project_id == project.id)
    ):
        raise ValidationFailed("the project has no requirements yet; upload a source document first")
    active = db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.project_id == project.id,
            WorkflowRun.definition == "delivery",
            WorkflowRun.status.in_(ACTIVE),
        )
    )
    if active is not None:
        raise Conflict(
            "a delivery run is already active for this project", details={"run_id": str(active.id)}
        )
    run_id = uuid.uuid4()
    run = WorkflowRun(
        id=run_id,
        project_id=project.id,
        definition="delivery",
        definition_version=DEFINITION_VERSION,
        status="queued",
        thread_id=f"delivery:{run_id}",
        created_by=principal.user_id,
        config={
            "repository_id": str(repo.id),
            "repository": repo.name,
            "limits": run_limits(project),
            "sandbox_backend": get_settings().sandbox_backend,
        },
        counters={},
    )
    db.add(run)
    db.flush()
    jobs.enqueue(
        db,
        kind="delivery.start",
        dedup_key=f"run:{run.id}:start",
        run_id=run.id,
        max_attempts=5,
        payload={"run_id": str(run.id)},
    )
    audit.record(
        db,
        action="run.started",
        actor_id=principal.user_id,
        actor_type="human",
        project_id=project.id,
        run_id=run.id,
        target_type="workflow_run",
        target_id=run.id,
        details={"repository": repo.name, "limits": run.config["limits"]},
    )
    events.emit(
        db, type="run.queued", project_id=project.id, run_id=run.id, payload={"repository": repo.name}
    )
    return run


def cancel_run(db: Session, principal: Principal, run: WorkflowRun, reason: str) -> WorkflowRun:
    if run.status in TERMINAL:
        raise Conflict(f"run is already {run.status}")
    run.status = "cancelled"
    run.finished_at = utcnow()
    run.outcome = {**run.outcome, "cancelled": {"by": principal.email, "reason": reason}}
    approvals.cancel_for_run(db, run.id)
    for job in db.scalars(
        select(WorkflowJob).where(WorkflowJob.run_id == run.id, WorkflowJob.status == "queued")
    ):
        job.status = "done"
        job.last_error = "run cancelled"
    audit.record(
        db,
        action="run.cancelled",
        actor_id=principal.user_id,
        actor_type="human",
        project_id=run.project_id,
        run_id=run.id,
        target_type="workflow_run",
        target_id=run.id,
        details={"reason": reason},
    )
    events.emit(
        db, type="run.cancelled", project_id=run.project_id, run_id=run.id, payload={"reason": reason}
    )
    return run


def mark_failed(run_id: uuid.UUID, error: str) -> None:
    with session_scope() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.status in TERMINAL:
            return
        run.status, run.error, run.finished_at = "failed", error[:4000], utcnow()
        events.emit(
            db,
            type="run.failed",
            project_id=run.project_id,
            run_id=run.id,
            payload={"stage": run.current_stage},
        )


# --- node infrastructure ------------------------------------------------------------------------------


def _budget(db: Session, run: WorkflowRun) -> BudgetTracker:
    limits = run.config.get("limits", {})
    used = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(TokenUsage.input_tokens + TokenUsage.output_tokens), 0),
            func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
        ).where(TokenUsage.run_id == run.id)
    ).one()
    counters = {
        **run.counters,
        "model_calls": int(used[0]),
        "tokens": int(used[1]),
        "cost_usd": float(used[2]),
        "elapsed_s": float(run.counters.get("active_s", 0)),
    }
    return BudgetTracker(
        Limits(
            max_model_calls=limits.get("max_model_calls", 200),
            max_tokens=limits.get("max_tokens", 2_000_000),
            max_cost_usd=limits.get("max_cost_usd", 25.0),
            max_wall_clock_s=limits.get("max_wall_clock_s", 3600),
        ),
        counters=counters,
    )


@contextmanager
def stage(run_id: str, name: str) -> Iterator[tuple[Session, WorkflowRun, BudgetTracker]]:
    with session_scope() as db:
        run = db.get(WorkflowRun, uuid.UUID(run_id))
        if run is None or run.status in TERMINAL:
            raise RunStopped(run.status if run else "missing")
        budget = _budget(db, run)
        budget.check_time()
        if budget.counters["model_calls"] > budget.limits.max_model_calls:
            raise BudgetExceeded("model call budget exhausted")
        if run.current_stage != name or run.status != "running":
            events.emit(
                db,
                type="workflow.stage_started",
                project_id=run.project_id,
                run_id=run.id,
                payload={"stage": name},
            )
        run.status = "running"
        run.current_stage = name
        run.started_at = run.started_at or utcnow()
        yield db, run, budget


def escalate_now(db: Session, run: WorkflowRun, stage_name: str, reason: str) -> None:
    """Escalate inside the current transaction (keeps records written by the stage, e.g. a rejected patch)."""
    run.status = "escalated"
    run.current_stage = stage_name
    run.finished_at = utcnow()
    run.outcome = {**run.outcome, "escalation": {"stage": stage_name, "reason": reason[:2000]}}
    audit.record(
        db,
        action="run.escalated",
        actor_id=None,
        actor_type="system",
        project_id=run.project_id,
        run_id=run.id,
        target_type="workflow_run",
        target_id=run.id,
        details={"stage": stage_name, "reason": reason[:500]},
    )
    events.emit(
        db,
        type="run.escalated",
        project_id=run.project_id,
        run_id=run.id,
        payload={"stage": stage_name, "reason": reason[:500]},
    )


def escalate(run_id: str, stage_name: str, reason: str) -> None:
    with session_scope() as db:
        run = db.get(WorkflowRun, uuid.UUID(run_id))
        if run is None or run.status in TERMINAL:
            return
        run.status = "escalated"
        run.current_stage = stage_name
        run.finished_at = utcnow()
        run.outcome = {**run.outcome, "escalation": {"stage": stage_name, "reason": reason[:2000]}}
        audit.record(
            db,
            action="run.escalated",
            actor_id=None,
            actor_type="system",
            project_id=run.project_id,
            run_id=run.id,
            target_type="workflow_run",
            target_id=run.id,
            details={"stage": stage_name, "reason": reason[:500]},
        )
        events.emit(
            db,
            type="run.escalated",
            project_id=run.project_id,
            run_id=run.id,
            payload={"stage": stage_name, "reason": reason[:500]},
        )


def node(
    name: str,
) -> Callable[[Callable[[DeliveryState], DeliveryState]], Callable[[DeliveryState], DeliveryState]]:
    def deco(fn: Callable[[DeliveryState], DeliveryState]) -> Callable[[DeliveryState], DeliveryState]:
        def wrapper(state: DeliveryState) -> DeliveryState:
            started = time.monotonic()
            try:
                return fn(state)
            except GraphInterrupt:
                raise
            except RunStopped:
                return {"halt": True}
            except (Escalate, BudgetExceeded, SkillError) as exc:
                escalate(state["run_id"], name, f"{type(exc).__name__}: {exc}")
                return {"halt": True}
            except DomainError as exc:
                escalate(state["run_id"], name, f"{type(exc).__name__}: {exc.message}")
                return {"halt": True}
            finally:
                elapsed = time.monotonic() - started
                with session_scope() as db:
                    run = db.get(WorkflowRun, uuid.UUID(state["run_id"]))
                    if run is not None:
                        run.counters = {
                            **run.counters,
                            "active_s": round(run.counters.get("active_s", 0) + elapsed, 3),
                        }

        wrapper.__name__ = name
        return wrapper

    return deco


def agent_ctx(
    db: Session, run: WorkflowRun, agent: str, stage_name: str, budget: BudgetTracker, **kw: Any
) -> ExecutionContext:
    return ExecutionContext(
        db=db, project_id=run.project_id, agent=agent, run_id=run.id, stage=stage_name, budget=budget, **kw
    )


def _agent_user(db: Session, agent: str) -> uuid.UUID | None:
    u = db.scalar(select(User.id).where(User.email == agent_email(agent)))
    return u


def _set_outcome(run: WorkflowRun, **values: Any) -> None:
    run.outcome = {**run.outcome, **values}


# --- nodes --------------------------------------------------------------------------------------------


@node("snapshot_inputs")
def snapshot_inputs(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "snapshot_inputs") as (db, run, _):
        if not run.input_artifacts:
            snap = requirements_snapshot(db, run.project_id)
            run.input_artifacts = {
                "requirements": {r["key"]: r["version"] for r in snap},
                "criteria": {c["key"]: c["version"] for r in snap for c in r["criteria"]},
                "captured_at": utcnow().isoformat(),
            }
            audit.record(
                db,
                action="run.inputs_snapshotted",
                actor_id=None,
                actor_type="system",
                project_id=run.project_id,
                run_id=run.id,
                target_type="workflow_run",
                target_id=run.id,
                details={"requirements": run.input_artifacts["requirements"]},
            )
    return {"outcome": "ok"}


def _blocking_questions(db: Session, project_id: uuid.UUID) -> list[OpenQuestion]:
    return list(
        db.scalars(
            select(OpenQuestion).where(
                OpenQuestion.project_id == project_id, OpenQuestion.status == "open", OpenQuestion.blocking
            )
        )
    )


@node("clarification_gate")
def clarification_gate(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "clarification_gate") as (db, run, _):
        blocking = _blocking_questions(db, run.project_id)
        if not blocking:
            return {"outcome": "clear"}
        run.status = "awaiting_approval"
        ids = [str(q.id) for q in blocking]
        events.emit(
            db,
            type="clarification.required",
            project_id=run.project_id,
            run_id=run.id,
            payload={"questions": ids},
        )
    interrupt({"gate": "requirements_clarification", "questions": ids})
    return {"outcome": "recheck"}


@node("plan_backlog")
def plan_backlog(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "plan_backlog") as (db, run, budget):
        project = db.get(Project, run.project_id)
        repo = db.get(ProjectRepository, uuid.UUID(run.config["repository_id"]))
        assert project is not None
        snap_keys = set(run.input_artifacts["requirements"])
        snapshot = [r for r in requirements_snapshot(db, run.project_id) if r["key"] in snap_keys]
        summary = generate_backlog(
            db, project, run_id=run.id, snapshot=snapshot, repository=repo, budget=budget
        )
        stories = (
            db.scalars(
                select(BacklogItem)
                .join(
                    backlog_item_requirements, backlog_item_requirements.c.backlog_item_id == BacklogItem.id
                )
                .join(Requirement, Requirement.id == backlog_item_requirements.c.requirement_id)
                .where(
                    BacklogItem.project_id == run.project_id,
                    BacklogItem.kind.in_(["story", "task", "enhancement"]),
                    Requirement.key.in_(snap_keys),
                )
                .order_by(BacklogItem.created_at)
            )
            .unique()
            .all()
        )
        _set_outcome(
            run, plan={k: v for k, v in summary.items() if k != "items"}, stories=[s.key for s in stories]
        )
    return {"outcome": "ok"}


def _repository_summary(output: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in output.items() if k != "modules"} | {
        "modules": [
            {"path": m["path"], "classes": m.get("classes", []), "functions": m.get("functions", [])}
            for m in output["modules"]
        ]
    }


@node("analyze_repository")
def analyze_repository(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "analyze_repository") as (db, run, budget):
        if "repository" not in run.outcome:
            result = SkillRuntime().execute(
                "repository-exploration",
                {"repository_id": run.config["repository_id"]},
                agent_ctx(db, run, "architect", "analyze_repository", budget),
            )
            _set_outcome(run, repository=_repository_summary(result.output))
    return {"outcome": "ok"}


def _scope_requirements(db: Session, run: WorkflowRun) -> list[dict[str, Any]]:
    keys = set(run.input_artifacts["requirements"])
    return [r for r in requirements_snapshot(db, run.project_id) if r["key"] in keys]


@node("propose_architecture")
def propose_architecture(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "propose_architecture") as (db, run, budget):
        project = db.get(Project, run.project_id)
        assert project is not None
        runtime = SkillRuntime()
        ctx = agent_ctx(db, run, "architect", "propose_architecture", budget)
        reqs = _scope_requirements(db, run)
        design = runtime.execute(
            "architecture-design",
            {
                "scope_title": project.name,
                "repository": run.outcome["repository"],
                "requirements": [
                    {k: r[k] for k in ("key", "kind", "title", "statement", "priority", "criteria")}
                    for r in reqs
                ],
                "review_feedback": state.get("feedback", []),
            },
            ctx,
        )
        author = _agent_user(db, "architect")
        proposal = arch.save_proposal(
            db,
            project_id=run.project_id,
            run_id=run.id,
            content=design.output,
            generated_by={
                "agent": "architect",
                "skill": "architecture-design",
                "strategy": design.strategy,
                "model_ref": design.model_ref,
                "is_mock": design.is_mock,
            },
            author_id=author,
            reason="generated" if not state.get("feedback") else "revised after review",
        )
        adr = runtime.execute("adr-generation", {"proposal": design.output}, ctx)
        record = arch.save_adr(db, project_id=run.project_id, proposal=proposal, content=adr.output)
        _set_outcome(run, proposal_id=str(proposal.id), adr_id=str(record.id))
    return {"outcome": "ok", "feedback": []}


def _gate(
    state: DeliveryState,
    *,
    gate: str,
    artifact_type: str,
    artifact_id_key: str,
    action: str,
    on_approved: Callable[[Session, WorkflowRun], None],
    on_changes: Callable[[Session, WorkflowRun, str], str],
) -> DeliveryState:
    """Generic approval gate. Returns outcome approved | revise | rejected | wait."""
    with stage(state["run_id"], gate + "_gate") as (db, run, _):
        artifact_id = uuid.UUID(run.outcome[artifact_id_key])
        req = approvals.ensure_request(
            db,
            project_id=run.project_id,
            run_id=run.id,
            gate=gate,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            requested_action=action,
        )
        if req.status == "pending":
            run.status = "awaiting_approval"
        request_id = req.id
    if req.status == "pending":
        interrupt({"gate": gate, "request_id": str(request_id)})
    with stage(state["run_id"], gate + "_gate") as (db, run, _):
        latest = approvals.latest_request(db, run.id, gate)
        if latest is None or latest.status in ("pending", "stale", "cancelled"):
            return {"outcome": "wait"}
        decision = approvals.decision_for(db, latest.id)
        assert decision is not None
        if latest.status == "approved":
            on_approved(db, run)
            return {"outcome": "approved"}
        if latest.status == "rejected":
            run.status = "cancelled"
            run.finished_at = utcnow()
            _set_outcome(run, rejected={"gate": gate, "comment": decision.comment})
            events.emit(
                db, type="run.rejected", project_id=run.project_id, run_id=run.id, payload={"gate": gate}
            )
            return {"outcome": "rejected", "halt": True}
        result = on_changes(db, run, decision.comment)
        return {"outcome": result, "feedback": [decision.comment]}


@node("architecture_gate")
def architecture_gate(state: DeliveryState) -> DeliveryState:
    def approved(db: Session, run: WorkflowRun) -> None:
        proposal = db.get(arch.ArchitectureProposal, uuid.UUID(run.outcome["proposal_id"]))
        assert proposal is not None
        proposal.status = "approved"
        adr = db.get(arch.ArchitectureDecisionRecord, uuid.UUID(run.outcome["adr_id"]))
        if adr is not None:
            adr.status = "accepted"

    def changes(db: Session, run: WorkflowRun, comment: str) -> str:
        proposal = db.get(arch.ArchitectureProposal, uuid.UUID(run.outcome["proposal_id"]))
        assert proposal is not None
        proposal.status = "changes_requested"
        revisions = int(run.counters.get("architecture_revisions", 0)) + 1
        run.counters = {**run.counters, "architecture_revisions": revisions}
        if revisions > run.config["limits"]["max_architecture_revisions"]:
            raise Escalate(
                f"architecture revision limit ({run.config['limits']['max_architecture_revisions']}) reached"
            )
        return "revise"

    return _gate(
        state,
        gate="architecture",
        artifact_type="architecture_proposal",
        artifact_id_key="proposal_id",
        action="Approve the architecture proposal and ADR before implementation planning.",
        on_approved=approved,
        on_changes=changes,
    )


@node("plan_implementation")
def plan_implementation(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "plan_implementation") as (db, run, budget):
        proposal = db.get(arch.ArchitectureProposal, uuid.UUID(run.outcome["proposal_id"]))
        assert proposal is not None
        keys = run.outcome.get("stories", [])
        items = list(
            db.scalars(
                select(BacklogItem).where(BacklogItem.project_id == run.project_id, BacklogItem.key.in_(keys))
            )
        )
        by_id = {i.id: i for i in items}
        stories = []
        for i in items:
            reqs = db.scalars(
                select(Requirement.key)
                .join(backlog_item_requirements, backlog_item_requirements.c.requirement_id == Requirement.id)
                .where(backlog_item_requirements.c.backlog_item_id == i.id)
            ).all()
            crits = db.scalars(
                select(AcceptanceCriterion.key)
                .join(
                    backlog_item_criteria,
                    backlog_item_criteria.c.acceptance_criterion_id == AcceptanceCriterion.id,
                )
                .where(backlog_item_criteria.c.backlog_item_id == i.id)
            ).all()
            stories.append(
                {
                    "key": i.key,
                    "title": i.title,
                    "requirement_keys": list(reqs),
                    "criteria_keys": list(crits),
                    "coding_agent": (i.model_route or {}).get("coding_agent"),
                }
            )
        edges = [
            (by_id[d.item_id].key, by_id[d.depends_on_id].key)
            for d in db.scalars(select(BacklogDependency).where(BacklogDependency.item_id.in_(list(by_id))))
            if d.depends_on_id in by_id
        ]
        layers = topological_layers([i.key for i in items], edges)
        result = SkillRuntime().execute(
            "implementation-planning",
            {"proposal": proposal.content, "stories": stories, "layers": layers},
            agent_ctx(db, run, "architect", "plan_implementation", budget),
        )
        if not result.output["in_scope"]:
            raise Escalate(
                "no backlog item can be executed by an available coding agent: "
                + "; ".join(f"{o['key']}: {o['reason']}" for o in result.output["out_of_scope"])
            )
        plan = arch.save_plan(
            db,
            project_id=run.project_id,
            run_id=run.id,
            proposal_id=proposal.id,
            content=result.output,
            author_id=_agent_user(db, "architect"),
            reason="generated from approved architecture",
        )
        _set_outcome(run, plan_id=str(plan.id))
    return {"outcome": "ok"}


@node("implementation_gate")
def implementation_gate(state: DeliveryState) -> DeliveryState:
    def approved(db: Session, run: WorkflowRun) -> None:
        plan = db.get(arch.ImplementationPlan, uuid.UUID(run.outcome["plan_id"]))
        assert plan is not None
        plan.status = "approved"
        _set_outcome(run, approved_scope=plan.content["in_scope"])

    def changes(db: Session, run: WorkflowRun, comment: str) -> str:
        # Scope changes are made by editing the plan (which invalidates the request); a bare change
        # request without an edit cannot be satisfied automatically, so it goes to a person.
        raise Escalate(f"implementation scope changes requested: {comment}")

    return _gate(
        state,
        gate="implementation_scope",
        artifact_type="implementation_plan",
        artifact_id_key="plan_id",
        action="Approve the implementation scope before any code is changed.",
        on_approved=approved,
        on_changes=changes,
    )


@node("finalize")
def finalize(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "finalize") as (db, run, budget):
        run.status = "completed"
        run.finished_at = utcnow()
        run.counters = {
            **run.counters,
            **{k: budget.counters[k] for k in ("model_calls", "tokens", "cost_usd")},
        }
        audit.record(
            db,
            action="run.completed",
            actor_id=None,
            actor_type="system",
            project_id=run.project_id,
            run_id=run.id,
            target_type="workflow_run",
            target_id=run.id,
            details={"counters": run.counters, "stage_reached": "finalize"},
        )
        events.emit(db, type="run.completed", project_id=run.project_id, run_id=run.id, payload={})
    return {"outcome": "ok"}


# --- graph --------------------------------------------------------------------------------------------


def _route(mapping: dict[str, str]) -> Callable[[DeliveryState], str]:
    def router(state: DeliveryState) -> str:
        if state.get("halt"):
            return END
        return mapping[state.get("outcome", "ok")]

    return router


def build_graph() -> StateGraph[DeliveryState]:
    from agentic_sdlc.workflows import stages_build as build

    g = StateGraph(DeliveryState)
    nodes = (
        snapshot_inputs,
        clarification_gate,
        plan_backlog,
        analyze_repository,
        propose_architecture,
        architecture_gate,
        plan_implementation,
        implementation_gate,
        build.implement,
        build.qa_tests,
        build.ba_verification,
        build.create_defects,
        build.regression,
        finalize,
    )
    for fn in nodes:
        g.add_node(fn.__name__, cast(Any, fn))
    g.add_edge(START, "snapshot_inputs")
    g.add_conditional_edges("snapshot_inputs", _route({"ok": "clarification_gate"}))
    g.add_conditional_edges(
        "clarification_gate", _route({"clear": "plan_backlog", "recheck": "clarification_gate"})
    )
    g.add_conditional_edges("plan_backlog", _route({"ok": "analyze_repository"}))
    g.add_conditional_edges("analyze_repository", _route({"ok": "propose_architecture"}))
    g.add_conditional_edges("propose_architecture", _route({"ok": "architecture_gate"}))
    g.add_conditional_edges(
        "architecture_gate",
        _route(
            {
                "approved": "plan_implementation",
                "wait": "architecture_gate",
                "revise": "propose_architecture",
                "rejected": END,
            }
        ),
    )
    g.add_conditional_edges("plan_implementation", _route({"ok": "implementation_gate"}))
    g.add_conditional_edges(
        "implementation_gate",
        _route({"approved": "implement", "wait": "implementation_gate", "rejected": END}),
    )
    g.add_conditional_edges("implement", _route({"ok": "qa_tests"}))
    g.add_conditional_edges("qa_tests", _route({"ok": "ba_verification"}))
    g.add_conditional_edges(
        "ba_verification", _route({"verified": "regression", "defects": "create_defects"})
    )
    g.add_conditional_edges("create_defects", _route({"repair": "implement"}))
    g.add_conditional_edges("regression", _route({"ok": "finalize"}))
    g.add_edge("finalize", END)
    return g


# --- job handlers -------------------------------------------------------------------------------------


class RunBusy(Exception):
    """Another worker holds this run's lock; the job is retried later."""


def _lock_key(run_id: uuid.UUID) -> int:
    return int.from_bytes(hashlib.sha256(run_id.bytes).digest()[:8], "big", signed=True)


def _invoke(run_id: uuid.UUID, value: Any) -> None:
    with checkpointer() as cp:
        conn = cast(Connection[Any], cp.conn)
        locked = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (_lock_key(run_id),)).fetchone()
        if not locked or not locked["ok"]:
            raise RunBusy(str(run_id))
        try:
            graph = build_graph().compile(checkpointer=cp)
            config: RunnableConfig = {
                "configurable": {"thread_id": f"delivery:{run_id}"},
                "recursion_limit": 200,
            }
            snapshot = graph.get_state(config)
            if value is None and snapshot.values:
                if snapshot.next and not any(t.interrupts for t in snapshot.tasks):
                    graph.invoke(None, config)  # crashed mid-run: continue from the last checkpoint
                return
            if value is None:
                graph.invoke({"run_id": str(run_id)}, config)
            elif snapshot.next and any(t.interrupts for t in snapshot.tasks):
                graph.invoke(Command(resume=value), config)
            elif snapshot.next:
                graph.invoke(None, config)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(run_id),))


def handle_start(payload: dict[str, Any], run_id: uuid.UUID | None) -> None:
    rid = uuid.UUID(payload["run_id"])
    with session_scope() as db:
        run = db.get(WorkflowRun, rid)
        if run is None or run.status in TERMINAL:
            return
    _invoke(rid, None)


def handle_resume(payload: dict[str, Any], run_id: uuid.UUID | None) -> None:
    rid = uuid.UUID(payload["run_id"])
    with session_scope() as db:
        run = db.get(WorkflowRun, rid)
        if run is None or run.status in TERMINAL:
            return
    _invoke(rid, {"reason": payload.get("reason"), "request_id": payload.get("request_id")})


def on_job_failed(kind: str, payload: dict[str, Any], error: str) -> None:
    if kind.startswith("delivery.") and payload.get("run_id"):
        log(logger, logging.ERROR, "delivery job failed permanently", run_id=payload["run_id"], error=error)
        mark_failed(uuid.UUID(payload["run_id"]), error)


def wake_runs_waiting_for_clarification(db: Session, project_id: uuid.UUID, cause: str) -> None:
    for run in db.scalars(
        select(WorkflowRun).where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.status == "awaiting_approval",
            WorkflowRun.current_stage == "clarification_gate",
        )
    ):
        jobs.enqueue(
            db,
            kind="delivery.resume",
            dedup_key=f"{cause}:{run.id}",
            run_id=run.id,
            max_attempts=10,
            payload={"run_id": str(run.id), "reason": "clarification"},
        )
