from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from agentic_sdlc.api.deps import DB, CurrentPrincipal, project_permission
from agentic_sdlc.api.schemas import ORMModel
from agentic_sdlc.approvals import service as approvals
from agentic_sdlc.architecture import service as arch
from agentic_sdlc.core.errors import NotFound
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import (
    AgentRun,
    ApprovalDecision,
    ApprovalRequest,
    ArchitectureDecisionRecord,
    ArchitectureProposal,
    ArtifactRevision,
    ImplementationPlan,
    Project,
    ToolInvocation,
    User,
    WorkflowEvent,
    WorkflowRun,
)
from agentic_sdlc.skills.registry import get_skill_registry
from agentic_sdlc.workflows import delivery

router = APIRouter(tags=["workflow"])
Read = Annotated[Project, Depends(project_permission("workflow.read"))]


class RunCreate(BaseModel):
    repository_id: uuid.UUID


class RunCancel(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class RunOut(ORMModel):
    id: uuid.UUID
    definition: str
    definition_version: int
    status: str
    current_stage: str | None
    config: dict[str, Any]
    input_artifacts: dict[str, Any]
    counters: dict[str, Any]
    outcome: dict[str, Any]
    error: str | None
    created_by: uuid.UUID
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AgentRunOut(ORMModel):
    id: uuid.UUID
    stage: str | None
    agent: str
    skill_id: str
    skill_version: str
    strategy: str
    model_ref: str | None
    is_mock: bool
    status: str
    attempts: int
    duration_ms: int | None
    routing: dict[str, Any]
    error: str | None
    created_at: datetime


class RunDetail(RunOut):
    agent_runs: list[AgentRunOut]
    tool_invocations: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    approvals: list[dict[str, Any]]


class ApprovalOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID | None
    gate: str
    requested_action: str
    artifact_type: str
    artifact_id: uuid.UUID
    artifact_version: int
    content_hash: str
    required_roles: list[str]
    status: str
    expires_at: datetime | None
    created_at: datetime
    can_decide: bool
    cannot_decide_reason: str | None
    decision: dict[str, Any] | None


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected", "changes_requested"]
    comment: str = Field(default="", max_length=4000)


def _get_run(db: DB, project_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None or run.project_id != project_id:
        raise NotFound("run not found")
    return run


@router.post("/projects/{project_id}/runs", response_model=RunOut, status_code=202)
def start_run(
    body: RunCreate,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("workflow.start"))],
) -> WorkflowRun:
    run = delivery.start_run(db, principal, project, repository_id=body.repository_id)
    db.commit()
    return run


@router.get("/projects/{project_id}/runs", response_model=list[RunOut])
def list_runs(project: Read, db: DB) -> list[WorkflowRun]:
    return list(
        db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project.id, WorkflowRun.definition == "delivery")
            .order_by(WorkflowRun.created_at.desc())
        )
    )


@router.get("/projects/{project_id}/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: uuid.UUID, project: Read, db: DB, principal: CurrentPrincipal) -> RunDetail:
    run = _get_run(db, project.id, run_id)
    agent_runs = db.scalars(
        select(AgentRun).where(AgentRun.run_id == run.id).order_by(AgentRun.created_at)
    ).all()
    tools = db.execute(
        select(ToolInvocation, AgentRun.agent)
        .join(AgentRun, AgentRun.id == ToolInvocation.agent_run_id)
        .where(AgentRun.run_id == run.id)
        .order_by(ToolInvocation.created_at)
    ).all()
    timeline = [
        {"seq": e.seq, "type": e.type, "ts": e.ts.isoformat(), "payload": e.payload}
        for e in db.scalars(
            select(WorkflowEvent).where(WorkflowEvent.run_id == run.id).order_by(WorkflowEvent.seq)
        )
    ]
    reqs = db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.run_id == run.id).order_by(ApprovalRequest.created_at)
    ).all()
    return RunDetail(
        **RunOut.model_validate(run).model_dump(),
        agent_runs=[AgentRunOut.model_validate(a) for a in agent_runs],
        tool_invocations=[
            {
                "tool": t.tool,
                "agent": agent,
                "policy_decision": t.policy_decision,
                "status": t.status,
                "duration_ms": t.duration_ms,
                "arguments": t.arguments,
                "at": t.created_at.isoformat(),
            }
            for t, agent in tools
        ],
        timeline=timeline,
        approvals=[_approval_out(db, principal, r).model_dump(mode="json") for r in reqs],
    )


@router.post("/projects/{project_id}/runs/{run_id}/cancel", response_model=RunOut)
def cancel(
    run_id: uuid.UUID,
    body: RunCancel,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("workflow.cancel"))],
) -> WorkflowRun:
    run = _get_run(db, project.id, run_id)
    delivery.cancel_run(db, principal, run, body.reason)
    db.commit()
    return run


def _approval_out(db: DB, principal: CurrentPrincipal, req: ApprovalRequest) -> ApprovalOut:
    reason: str | None = None
    if req.status != "pending":
        reason = f"request is {req.status}"
    elif not (principal.roles_in(db, req.project_id) & set(req.required_roles)):
        reason = "requires role: " + " or ".join(r.replace("_", " ") for r in req.required_roles)
    elif req.gate == "release_production" and not get_settings().production_deploy_enabled:
        reason = "production deployment is disabled by policy"
    else:
        _, _, author = approvals.current_artifact(db, req.artifact_type, req.artifact_id)
        if req.gate in approvals.SEPARATION_OF_DUTIES and author == principal.user_id:
            reason = "you authored this revision"
    d = approvals.decision_for(db, req.id)
    decision = None
    if d is not None:
        approver = db.get(User, d.approver_id)
        decision = {
            "decision": d.decision,
            "comment": d.comment,
            "approver": approver.display_name if approver else None,
            "role": d.approver_role,
            "at": d.created_at.isoformat(),
            "content_hash_at_decision": d.content_hash_at_decision,
        }
    return ApprovalOut(
        id=req.id,
        run_id=req.run_id,
        gate=req.gate,
        requested_action=req.requested_action,
        artifact_type=req.artifact_type,
        artifact_id=req.artifact_id,
        artifact_version=req.artifact_version,
        content_hash=req.content_hash,
        required_roles=req.required_roles,
        status=req.status,
        expires_at=req.expires_at,
        created_at=req.created_at,
        can_decide=reason is None,
        cannot_decide_reason=reason,
        decision=decision,
    )


@router.get("/projects/{project_id}/approvals", response_model=list[ApprovalOut])
def list_approvals(
    project: Read,
    db: DB,
    principal: CurrentPrincipal,
    status: Annotated[Literal["pending", "all"], Query()] = "pending",
) -> list[ApprovalOut]:
    stmt = select(ApprovalRequest).where(ApprovalRequest.project_id == project.id)
    if status == "pending":
        stmt = stmt.where(ApprovalRequest.status == "pending")
    return [
        _approval_out(db, principal, r) for r in db.scalars(stmt.order_by(ApprovalRequest.created_at.desc()))
    ]


@router.post("/projects/{project_id}/approvals/{request_id}/decision", response_model=ApprovalOut)
def decide(
    request_id: uuid.UUID, body: DecisionIn, project: Read, db: DB, principal: CurrentPrincipal
) -> ApprovalOut:
    req = db.get(ApprovalRequest, request_id)
    if req is None or req.project_id != project.id:
        raise NotFound("approval request not found")
    approvals.decide(db, principal, req, decision=body.decision, comment=body.comment)
    db.commit()
    return _approval_out(db, principal, req)


# --- architecture artifacts ---------------------------------------------------------------------------


class ProposalOut(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID | None
    title: str
    status: str
    version: int
    content: dict[str, Any]
    content_hash: str
    generated_by: dict[str, Any]
    updated_at: datetime


class ProposalEdit(BaseModel):
    expected_version: int
    content: dict[str, Any]


class PlanScopeEdit(BaseModel):
    expected_version: int
    remove: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/projects/{project_id}/architecture", response_model=list[ProposalOut])
def list_proposals(project: Read, db: DB) -> list[ArchitectureProposal]:
    return list(
        db.scalars(
            select(ArchitectureProposal)
            .where(ArchitectureProposal.project_id == project.id)
            .order_by(ArchitectureProposal.created_at.desc())
        )
    )


@router.get("/projects/{project_id}/architecture/{proposal_id}")
def get_proposal(proposal_id: uuid.UUID, project: Read, db: DB) -> dict[str, Any]:
    p = arch.get_proposal(db, project.id, proposal_id)
    revisions = db.scalars(
        select(ArtifactRevision)
        .where(
            ArtifactRevision.artifact_type == "architecture_proposal", ArtifactRevision.artifact_id == p.id
        )
        .order_by(ArtifactRevision.version)
    ).all()
    adr = db.scalar(select(ArchitectureDecisionRecord).where(ArchitectureDecisionRecord.proposal_id == p.id))
    plan = db.scalar(select(ImplementationPlan).where(ImplementationPlan.proposal_id == p.id))
    authors = {
        u.id: u.display_name
        for u in db.scalars(select(User).where(User.id.in_([r.author_id for r in revisions if r.author_id])))
    }
    return {
        "proposal": ProposalOut.model_validate(p).model_dump(mode="json"),
        "revisions": [
            {
                "version": r.version,
                "content_hash": r.content_hash,
                "reason": r.reason,
                "author": authors.get(r.author_id) if r.author_id else None,
                "created_at": r.created_at.isoformat(),
                "content": r.content,
            }
            for r in revisions
        ],
        "adr": None
        if adr is None
        else {
            "id": str(adr.id),
            "number": adr.number,
            "title": adr.title,
            "status": adr.status,
            "version": adr.version,
            "content": adr.content,
        },
        "plan": None
        if plan is None
        else {
            "id": str(plan.id),
            "status": plan.status,
            "version": plan.version,
            "content": plan.content,
            "content_hash": plan.content_hash,
        },
    }


@router.patch("/projects/{project_id}/architecture/{proposal_id}", response_model=ProposalOut)
def edit_proposal(
    proposal_id: uuid.UUID,
    body: ProposalEdit,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("architecture.edit"))],
) -> ArchitectureProposal:
    p = arch.get_proposal(db, project.id, proposal_id)
    schema = get_skill_registry().get("architecture-design").output_schema
    arch.edit_proposal(
        db, principal, p, expected_version=body.expected_version, content=body.content, output_schema=schema
    )
    db.commit()
    db.refresh(p)
    return p


@router.patch("/projects/{project_id}/implementation-plans/{plan_id}/scope")
def narrow_scope(
    plan_id: uuid.UUID,
    body: PlanScopeEdit,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("project.update"))],
) -> dict[str, Any]:
    plan = db.get(ImplementationPlan, plan_id)
    if plan is None or plan.project_id != project.id:
        raise NotFound("plan not found")
    arch.edit_plan_scope(
        db, principal, plan, expected_version=body.expected_version, remove=body.remove, reason=body.reason
    )
    db.commit()
    return {"id": str(plan.id), "version": plan.version, "content": plan.content}


@router.get("/projects/{project_id}/adrs")
def list_adrs(project: Read, db: DB) -> list[dict[str, Any]]:
    return [
        {
            "id": str(a.id),
            "number": a.number,
            "title": a.title,
            "status": a.status,
            "version": a.version,
            "content": a.content,
            "proposal_id": str(a.proposal_id) if a.proposal_id else None,
        }
        for a in db.scalars(
            select(ArchitectureDecisionRecord)
            .where(ArchitectureDecisionRecord.project_id == project.id)
            .order_by(ArchitectureDecisionRecord.number)
        )
    ]


@router.get("/projects/{project_id}/approvals/{request_id}/decision-record")
def decision_record(request_id: uuid.UUID, project: Read, db: DB) -> dict[str, Any]:
    d = db.scalar(
        select(ApprovalDecision)
        .join(ApprovalRequest, ApprovalRequest.id == ApprovalDecision.request_id)
        .where(ApprovalRequest.id == request_id, ApprovalRequest.project_id == project.id)
    )
    if d is None:
        raise NotFound("no decision recorded")
    return {
        "decision": d.decision,
        "approver_id": str(d.approver_id),
        "role": d.approver_role,
        "comment": d.comment,
        "content_hash_at_decision": d.content_hash_at_decision,
        "at": d.created_at.isoformat(),
    }
