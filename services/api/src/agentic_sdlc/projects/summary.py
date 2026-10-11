"""Dashboard aggregates for a project. Read-only; computed from authoritative tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_sdlc.api.schemas import ProjectOut, ProjectSummary
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    ApprovalRequest,
    BacklogItem,
    Defect,
    OpenQuestion,
    Project,
    Requirement,
    SourceDocument,
    TokenUsage,
    WorkflowRun,
)


def _count(db: Session, model: Any, *conds: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*conds)) or 0)


def project_summary(db: Session, project: Project, my_roles: list[str]) -> ProjectSummary:
    pid = project.id
    active = db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == pid, WorkflowRun.definition == "delivery")
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    usage = db.execute(
        select(
            func.coalesce(func.sum(TokenUsage.input_tokens), 0),
            func.coalesce(func.sum(TokenUsage.output_tokens), 0),
            func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
            func.bool_or(TokenUsage.is_estimated),
        ).where(TokenUsage.project_id == pid)
    ).one()
    return ProjectSummary(
        project=ProjectOut.model_validate(project),
        my_roles=my_roles,
        counts={
            "documents": _count(db, SourceDocument, SourceDocument.project_id == pid),
            "requirements": _count(db, Requirement, Requirement.project_id == pid),
            "acceptance_criteria": _count(db, AcceptanceCriterion, AcceptanceCriterion.project_id == pid),
            "open_questions": _count(
                db, OpenQuestion, OpenQuestion.project_id == pid, OpenQuestion.status == "open"
            ),
            "backlog_items": _count(db, BacklogItem, BacklogItem.project_id == pid),
            "runs": _count(db, WorkflowRun, WorkflowRun.project_id == pid),
        },
        stage=active.current_stage if active else None,
        active_run=(
            {
                "id": str(active.id),
                "status": active.status,
                "stage": active.current_stage,
                "definition": active.definition,
            }
            if active
            else None
        ),
        pending_approvals=_count(
            db, ApprovalRequest, ApprovalRequest.project_id == pid, ApprovalRequest.status == "pending"
        ),
        open_defects=_count(db, Defect, Defect.project_id == pid, Defect.status == "open"),
        tokens={
            "input": int(usage[0]),
            "output": int(usage[1]),
            "cost_usd": float(usage[2]),
            "includes_estimates": bool(usage[3]) if usage[3] is not None else False,
        },
    )
