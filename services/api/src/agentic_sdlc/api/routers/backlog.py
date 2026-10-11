from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agentic_sdlc.api.deps import DB, CurrentPrincipal, project_permission
from agentic_sdlc.api.schemas import ORMModel
from agentic_sdlc.backlog import service
from agentic_sdlc.backlog.graph import critical_path, find_cycle, topological_layers
from agentic_sdlc.core.hashing import content_hash
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    BacklogDependency,
    BacklogItem,
    Project,
    Requirement,
    TokenUsage,
    backlog_item_criteria,
    backlog_item_requirements,
)
from agentic_sdlc.workflows import jobs

router = APIRouter(tags=["backlog"])

Read = Annotated[Project, Depends(project_permission("backlog.read"))]
Edit = Annotated[Project, Depends(project_permission("backlog.edit"))]


class BacklogItemOut(ORMModel):
    id: uuid.UUID
    key: str
    kind: str
    title: str
    description: str
    status: str
    priority: int
    labels: list[Any]
    origin: str
    parent_id: uuid.UUID | None
    run_id: uuid.UUID | None
    story_points: float | None
    uncertainty: str | None
    estimate: dict[str, Any]
    actuals: dict[str, Any]
    model_route: dict[str, Any]
    assigned_agent: str | None
    version: int
    requirement_keys: list[str] = []
    criteria_keys: list[str] = []
    depends_on: list[dict[str, Any]] = []
    updated_at: datetime


class BacklogOut(BaseModel):
    items: list[BacklogItemOut]
    analysis: dict[str, Any]


class ItemCreate(BaseModel):
    kind: Literal["epic", "feature", "story", "task", "bug", "enhancement"]
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    parent_id: uuid.UUID | None = None
    priority: int = Field(default=3, ge=1, le=5)
    labels: list[str] = []
    story_points: float | None = Field(default=None, ge=0, le=100)
    requirement_ids: list[uuid.UUID] = []


class ItemUpdate(BaseModel):
    expected_version: int
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: Literal["todo", "in_progress", "done", "blocked"] | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    labels: list[str] | None = None
    story_points: float | None = Field(default=None, ge=0, le=100)
    uncertainty: Literal["low", "medium", "high"] | None = None
    parent_id: uuid.UUID | None = None


class DependencyCreate(BaseModel):
    depends_on_id: uuid.UUID
    reason: str = ""


def _backlog(db: DB, project_id: uuid.UUID) -> BacklogOut:
    items = db.scalars(
        select(BacklogItem).where(BacklogItem.project_id == project_id).order_by(BacklogItem.created_at)
    ).all()
    req_keys: dict[uuid.UUID, list[str]] = {}
    for item_id, key in db.execute(
        select(backlog_item_requirements.c.backlog_item_id, Requirement.key)
        .join(Requirement, Requirement.id == backlog_item_requirements.c.requirement_id)
        .where(Requirement.project_id == project_id)
    ).all():
        req_keys.setdefault(item_id, []).append(key)
    crit_keys: dict[uuid.UUID, list[str]] = {}
    for item_id, key in db.execute(
        select(backlog_item_criteria.c.backlog_item_id, AcceptanceCriterion.key)
        .join(AcceptanceCriterion, AcceptanceCriterion.id == backlog_item_criteria.c.acceptance_criterion_id)
        .where(AcceptanceCriterion.project_id == project_id)
    ).all():
        crit_keys.setdefault(item_id, []).append(key)
    deps = db.scalars(select(BacklogDependency).where(BacklogDependency.project_id == project_id)).all()
    by_id = {i.id: i for i in items}
    out = []
    for i in items:
        o = BacklogItemOut.model_validate(i)
        o.requirement_keys = sorted(req_keys.get(i.id, []))
        o.criteria_keys = sorted(crit_keys.get(i.id, []))
        o.depends_on = [
            {"id": str(d.depends_on_id), "key": by_id[d.depends_on_id].key, "reason": d.reason}
            for d in deps
            if d.item_id == i.id and d.depends_on_id in by_id
        ]
        out.append(o)
    stories = [i.id for i in items if i.kind in ("story", "task", "bug", "enhancement")]
    edges = [(d.item_id, d.depends_on_id) for d in deps if d.item_id in by_id and d.depends_on_id in by_id]
    cycle = find_cycle(stories, edges)
    analysis: dict[str, Any] = {
        "valid": cycle is None,
        "cycle": [by_id[c].key for c in cycle] if cycle else None,
    }
    if cycle is None and stories:
        nodes = sorted({*stories, *(x for e in edges for x in e)}, key=lambda n: by_id[n].created_at)
        analysis["milestones"] = [[by_id[n].key for n in layer] for layer in topological_layers(nodes, edges)]
        weights = {n: float(by_id[n].story_points or 1) for n in nodes}
        analysis["critical_path"] = [by_id[n].key for n in critical_path(nodes, edges, weights)]
        analysis["totals"] = {
            "story_points": sum(float(by_id[n].story_points or 0) for n in stories),
            "tokens_estimated": sum(
                int((by_id[n].estimate or {}).get("tokens_in", 0))
                + int((by_id[n].estimate or {}).get("tokens_out", 0))
                for n in stories
            ),
            "tokens_actual": sum(int((by_id[n].actuals or {}).get("tokens_total", 0)) for n in stories),
        }
    return BacklogOut(items=out, analysis=analysis)


@router.get("/projects/{project_id}/backlog", response_model=BacklogOut)
def get_backlog(project: Read, db: DB) -> BacklogOut:
    return _backlog(db, project.id)


@router.post("/projects/{project_id}/backlog/generate", status_code=202)
def generate(project: Edit, db: DB) -> dict[str, str]:
    snapshot = service.requirements_snapshot(db, project.id)
    if not snapshot:
        from agentic_sdlc.core.errors import ValidationFailed

        raise ValidationFailed("there are no requirements to plan")
    key = "plan:" + content_hash([(r["key"], r["version"]) for r in snapshot])[:24] + f":{project.id}"
    job = jobs.enqueue(db, kind="plan", dedup_key=key, payload={"project_id": str(project.id)})
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.post("/projects/{project_id}/backlog/items", response_model=BacklogItemOut, status_code=201)
def create_item(body: ItemCreate, project: Edit, db: DB, principal: CurrentPrincipal) -> BacklogItem:
    item = service.create_item(db, principal, project, body.model_dump())
    db.commit()
    return item


@router.patch("/projects/{project_id}/backlog/items/{item_id}", response_model=BacklogItemOut)
def update_item(
    item_id: uuid.UUID, body: ItemUpdate, project: Edit, db: DB, principal: CurrentPrincipal
) -> BacklogItem:
    item = service.get_item(db, project.id, item_id)
    service.update_item(
        db,
        principal,
        item,
        expected_version=body.expected_version,
        changes=body.model_dump(exclude={"expected_version"}, exclude_unset=True),
    )
    db.commit()
    db.refresh(item)
    return item


@router.post("/projects/{project_id}/backlog/items/{item_id}/dependencies", status_code=201)
def add_dependency(
    item_id: uuid.UUID, body: DependencyCreate, project: Edit, db: DB, principal: CurrentPrincipal
) -> dict[str, str]:
    item = service.get_item(db, project.id, item_id)
    target = service.get_item(db, project.id, body.depends_on_id)
    service.add_dependency(db, principal, item, target, body.reason)
    db.commit()
    return {"item": item.key, "depends_on": target.key}


@router.delete("/projects/{project_id}/backlog/items/{item_id}/dependencies/{depends_on_id}", status_code=204)
def remove_dependency(
    item_id: uuid.UUID, depends_on_id: uuid.UUID, project: Edit, db: DB, principal: CurrentPrincipal
) -> None:
    item = service.get_item(db, project.id, item_id)
    service.remove_dependency(db, principal, item, depends_on_id)
    db.commit()


@router.get("/projects/{project_id}/usage")
def usage(project: Read, db: DB) -> dict[str, Any]:
    """Token and cost accounting. Estimated rows are flagged; nothing is shown as measured unless it was."""
    rows = db.execute(
        select(
            TokenUsage.model_id,
            TokenUsage.purpose,
            TokenUsage.is_estimated,
            func.count(),
            func.sum(TokenUsage.input_tokens),
            func.sum(TokenUsage.output_tokens),
            func.sum(TokenUsage.cost_usd),
        )
        .where(TokenUsage.project_id == project.id)
        .group_by(TokenUsage.model_id, TokenUsage.purpose, TokenUsage.is_estimated)
    ).all()
    return {
        "rows": [
            {
                "model_id": m,
                "purpose": p,
                "is_estimated": e,
                "calls": n,
                "input_tokens": int(i or 0),
                "output_tokens": int(o or 0),
                "cost_usd": float(c or 0),
            }
            for m, p, e, n, i, o, c in rows
        ],
        "limits": project.limits,
    }


# --- capability views (any authenticated user) ----------------------------------------------------


admin_router = APIRouter(tags=["capabilities"])


@admin_router.get("/skills")
def list_skills(_: CurrentPrincipal) -> list[dict[str, Any]]:
    from agentic_sdlc.skills.registry import get_skill_registry

    reg = get_skill_registry()
    return [
        {
            "id": m.id,
            "version": m.version,
            "name": m.name,
            "description": m.description,
            "category": m.category,
            "agents": m.agents,
            "tools": m.tools,
            "strategies": m.strategies,
            "model": m.model.model_dump() if m.model else None,
            "approval_level": m.approval_level,
            "timeout_s": m.timeout_s,
            "retry": m.retry.model_dump(),
            "budgets": m.budgets.model_dump(),
            "preconditions": m.preconditions,
            "postconditions": m.postconditions,
            "evaluation": m.evaluation.model_dump(),
            "triggers": m.triggers.model_dump(),
        }
        for m in sorted(reg.manifests.values(), key=lambda m: (m.category, m.id))
    ]


@admin_router.get("/agents")
def list_agents(_: CurrentPrincipal) -> dict[str, Any]:
    from agentic_sdlc.skills.registry import get_skill_registry

    return get_skill_registry().agents


@admin_router.get("/models")
def list_models(_: CurrentPrincipal, db: DB) -> list[dict[str, Any]]:
    from agentic_sdlc.models.registry import get_registry
    from agentic_sdlc.orm import ModelRegistryEntry

    reg = get_registry()
    observed = {r.model_id: r.observed for r in db.scalars(select(ModelRegistryEntry))}
    return [
        {**e.model_dump(), "available": reg.is_available(e), "observed": observed.get(e.model_id, {})}
        for e in reg.entries.values()
    ]
