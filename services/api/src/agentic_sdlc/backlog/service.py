"""Backlog generation (Planner agent) and human backlog management."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.backlog.graph import would_create_cycle
from agentic_sdlc.backlog.planning import calibration_factor
from agentic_sdlc.core.errors import Conflict, NotFound, ValidationFailed
from agentic_sdlc.events import service as events
from agentic_sdlc.execution.coding_agents import select_coding_agent
from agentic_sdlc.models.registry import get_registry
from agentic_sdlc.models.router import RoutingRequest, route
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    BacklogDependency,
    BacklogItem,
    OpenQuestion,
    Project,
    ProjectRepository,
    Requirement,
    backlog_item_criteria,
    backlog_item_requirements,
)
from agentic_sdlc.skills.base import ExecutionContext
from agentic_sdlc.skills.runtime import SkillRuntime

HIERARCHY = {
    "epic": None,
    "feature": {"epic"},
    "story": {"feature", "epic"},
    "task": {"story"},
    "bug": {"story", "feature", "epic", None},
    "enhancement": {"feature", "epic", None},
}


def requirements_snapshot(db: Session, project_id: uuid.UUID) -> list[dict[str, Any]]:
    """Requirements (non-superseded) with their non-rejected criteria, including versions for provenance."""
    out = []
    for r in db.scalars(
        select(Requirement)
        .where(Requirement.project_id == project_id, Requirement.status != "superseded")
        .order_by(Requirement.key)
    ):
        crits = db.scalars(
            select(AcceptanceCriterion)
            .where(AcceptanceCriterion.requirement_id == r.id, AcceptanceCriterion.status != "rejected")
            .order_by(AcceptanceCriterion.key)
        ).all()
        out.append(
            {
                "id": str(r.id),
                "key": r.key,
                "kind": r.kind,
                "title": r.title,
                "statement": r.statement,
                "priority": r.priority,
                "origin": r.origin,
                "version": r.version,
                "criteria": [
                    {"id": str(c.id), "key": c.key, "text": c.text, "origin": c.origin, "version": c.version}
                    for c in crits
                ],
            }
        )
    return out


def estimate_repo_context(repo: ProjectRepository | None) -> int:
    """Rough context needed to work on the repository: source text under 200 KB, in tokens (chars/4)."""
    if repo is None or repo.kind != "fixture":
        return 8000
    total = 0
    for p in Path(repo.location).rglob("*"):
        if (
            p.is_file()
            and ".git" not in p.parts
            and p.suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".txt"}
        ):
            total += p.stat().st_size
    return min(total, 200_000) // 4


def next_key(db: Session, project: Project) -> Any:
    n = (
        db.scalar(select(func.count()).select_from(BacklogItem).where(BacklogItem.project_id == project.id))
        or 0
    )
    while True:
        n += 1
        yield f"{project.key}-{n}"


def _calibration(db: Session, project_id: uuid.UUID) -> tuple[float, int]:
    history = []
    for item in db.scalars(
        select(BacklogItem).where(BacklogItem.project_id == project_id, BacklogItem.kind == "story")
    ):
        est = (item.estimate or {}).get("tokens_in", 0) + (item.estimate or {}).get("tokens_out", 0)
        act = (item.actuals or {}).get("tokens_total", 0)
        if est and act and not (item.actuals or {}).get("tokens_estimated", True):
            history.append((est, act))
    return calibration_factor(history)


def generate_backlog(
    db: Session,
    project: Project,
    *,
    run_id: uuid.UUID | None,
    snapshot: list[dict[str, Any]],
    repository: ProjectRepository | None,
    budget: Any = None,
) -> dict[str, Any]:
    """Run the Planner agent's skills and persist the backlog. Idempotent per run."""
    if run_id is not None:
        existing = db.scalars(select(BacklogItem).where(BacklogItem.run_id == run_id)).all()
        if existing:
            return {"reused": True, "items": [i.key for i in existing]}
    covered = set(
        db.scalars(
            select(Requirement.key)
            .join(backlog_item_requirements, backlog_item_requirements.c.requirement_id == Requirement.id)
            .where(Requirement.project_id == project.id)
        )
    )
    todo = [r for r in snapshot if r["key"] not in covered]
    if not todo:
        return {"reused": True, "items": [], "note": "all requirements already have backlog items"}

    runtime = SkillRuntime()
    ctx = ExecutionContext(
        db=db, project_id=project.id, agent="planner", run_id=run_id, stage="plan_backlog", budget=budget
    )
    epic_title = project.name if not covered else f"{project.name}: follow-up"
    decomposition = runtime.execute(
        "backlog-decomposition",
        {
            "project_name": project.name,
            "epic_title": epic_title,
            "requirements": [
                {k: r[k] for k in ("key", "kind", "title", "statement", "priority", "origin", "criteria")}
                for r in todo
            ],
        },
        ctx,
    )
    plan = decomposition.output

    code_route = route(
        get_registry(),
        RoutingRequest(
            task="code_generation",
            capabilities=["code", "structured_output"],
            context_tokens=estimate_repo_context(repository),
        ),
    )
    coding_agent = select_coding_agent(code_route, repository)
    entry = get_registry().get(code_route.model_id) if code_route.model_id else None
    calib, samples = _calibration(db, project.id)
    q_counts: dict[str, int] = {}
    for key, n in db.execute(
        select(Requirement.key, func.count())
        .join(OpenQuestion, OpenQuestion.requirement_id == Requirement.id)
        .where(Requirement.project_id == project.id, OpenQuestion.status == "open")
        .group_by(Requirement.key)
    ).all():
        q_counts[key] = n
    estimates = runtime.execute(
        "effort-estimation",
        {
            "stories": [
                {"ref": s["ref"], "requirement_keys": s["requirement_keys"]} for s in plan["stories"]
            ],
            "requirements": [
                {k: r[k] for k in ("key", "kind", "statement", "origin", "criteria")} for r in todo
            ],
            "open_questions": q_counts,
            "repo_context_tokens": estimate_repo_context(repository),
            "calibration_factor": calib,
            "price_in_per_1k": entry.pricing.input_per_1k_usd if entry else 0.0,
            "price_out_per_1k": entry.pricing.output_per_1k_usd if entry else 0.0,
        },
        ctx,
    ).output["estimates"]
    by_ref = {e["ref"]: e for e in estimates}
    stories_for_deps = [
        {"ref": s["ref"], "story_points": by_ref[s["ref"]]["story_points"]} for s in plan["stories"]
    ]
    deps = runtime.execute(
        "dependency-analysis", {"stories": stories_for_deps, "dependencies": plan["dependencies"]}, ctx
    ).output

    keys = next_key(db, project)
    req_ids = {r["key"]: uuid.UUID(r["id"]) for r in todo}
    crit_ids = {c["key"]: uuid.UUID(c["id"]) for r in todo for c in r["criteria"]}
    generated = {
        "strategy": decomposition.strategy,
        "model_ref": decomposition.model_ref,
        "is_mock": decomposition.is_mock,
    }
    epic = BacklogItem(
        project_id=project.id,
        run_id=run_id,
        key=next(keys),
        kind="epic",
        title=plan["epic"]["title"],
        description=plan["epic"]["description"],
        labels=plan["epic"].get("labels", []),
        priority=1,
        model_route={"generated_by": generated},
    )
    db.add(epic)
    db.flush()
    feature_ids: dict[str, uuid.UUID] = {}
    for f in plan["features"]:
        item = BacklogItem(
            project_id=project.id,
            run_id=run_id,
            parent_id=epic.id,
            key=next(keys),
            kind="feature",
            title=f["title"],
            description=f["description"],
            priority=2,
            model_route={"generated_by": generated},
        )
        db.add(item)
        db.flush()
        feature_ids[f["ref"]] = item.id
    story_ids: dict[str, uuid.UUID] = {}
    for s in plan["stories"]:
        est = by_ref[s["ref"]]
        item = BacklogItem(
            project_id=project.id,
            run_id=run_id,
            parent_id=feature_ids[s["feature_ref"]],
            key=next(keys),
            kind=s["kind"],
            title=s["title"],
            description=s["description"],
            priority=s["priority"],
            labels=s["labels"],
            story_points=est["story_points"],
            uncertainty=est["uncertainty"],
            estimate=est["estimate"],
            assigned_agent="developer",
            model_route={
                "generated_by": generated,
                "coding_agent": coding_agent,
                "routing": code_route.summary(),
                "calibration_samples": samples,
            },
        )
        db.add(item)
        db.flush()
        story_ids[s["ref"]] = item.id
        for k in s["requirement_keys"]:
            db.execute(
                insert(backlog_item_requirements).values(backlog_item_id=item.id, requirement_id=req_ids[k])
            )
        for k in s["criteria_keys"]:
            if k in crit_ids:
                db.execute(
                    insert(backlog_item_criteria).values(
                        backlog_item_id=item.id, acceptance_criterion_id=crit_ids[k]
                    )
                )
    for d in plan["dependencies"]:
        db.add(
            BacklogDependency(
                project_id=project.id,
                item_id=story_ids[d["from_ref"]],
                depends_on_id=story_ids[d["to_ref"]],
                reason=d["reason"],
            )
        )
    db.flush()
    ref_to_key = {ref: db.get(BacklogItem, i).key for ref, i in story_ids.items()}  # type: ignore[union-attr]
    summary = {
        "reused": False,
        "epic": epic.key,
        "stories": len(story_ids),
        "strategy": decomposition.strategy,
        "coding_agent": coding_agent,
        "milestones": [
            {"name": m["name"], "items": [ref_to_key[r] for r in m["refs"]]} for m in deps["milestones"]
        ],
        "critical_path": [ref_to_key[r] for r in deps["critical_path"]],
        "totals": {
            "story_points": sum(e["story_points"] for e in estimates),
            "tokens_estimated": sum(
                e["estimate"]["tokens_in"] + e["estimate"]["tokens_out"] for e in estimates
            ),
            "cost_usd_estimated": round(sum(e["estimate"]["cost_usd"] for e in estimates), 4),
        },
    }
    events.emit(
        db,
        type="backlog.generated",
        project_id=project.id,
        run_id=run_id,
        payload={"epic": epic.key, "stories": len(story_ids), "strategy": decomposition.strategy},
        artifact_refs=[{"type": "backlog_item", "id": str(epic.id)}],
    )
    return summary


# --- human management -----------------------------------------------------------------------------


def get_item(db: Session, project_id: uuid.UUID, item_id: uuid.UUID) -> BacklogItem:
    item = db.get(BacklogItem, item_id)
    if item is None or item.project_id != project_id:
        raise NotFound("backlog item not found")
    return item


def _check_parent(db: Session, project_id: uuid.UUID, kind: str, parent_id: uuid.UUID | None) -> None:
    allowed = HIERARCHY.get(kind)
    if kind == "epic":
        if parent_id is not None:
            raise ValidationFailed("an epic cannot have a parent")
        return
    parent_kind = get_item(db, project_id, parent_id).kind if parent_id else None
    if allowed is not None and parent_kind not in allowed:
        raise ValidationFailed(
            f"a {kind} cannot be placed under {parent_kind or 'the root'}",
            details={"allowed_parents": sorted(str(a) for a in allowed)},
        )


def create_item(db: Session, principal: Principal, project: Project, data: dict[str, Any]) -> BacklogItem:
    _check_parent(db, project.id, data["kind"], data.get("parent_id"))
    item = BacklogItem(
        project_id=project.id,
        key=next(next_key(db, project)),
        kind=data["kind"],
        title=data["title"],
        description=data.get("description", ""),
        priority=data.get("priority", 3),
        parent_id=data.get("parent_id"),
        labels=data.get("labels", []),
        story_points=data.get("story_points"),
        origin="human",
    )
    db.add(item)
    db.flush()
    for rid in data.get("requirement_ids", []):
        req = db.get(Requirement, rid)
        if req is None or req.project_id != project.id:
            raise ValidationFailed("unknown requirement")
        db.execute(insert(backlog_item_requirements).values(backlog_item_id=item.id, requirement_id=rid))
    audit.record(
        db,
        action="backlog.item_created",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="backlog_item",
        target_id=item.id,
        details={"key": item.key},
    )
    events.emit(db, type="backlog.item_created", project_id=project.id, payload={"key": item.key})
    return item


def update_item(
    db: Session, principal: Principal, item: BacklogItem, *, expected_version: int, changes: dict[str, Any]
) -> BacklogItem:
    if item.version != expected_version:
        raise Conflict("backlog item was modified by someone else", details={"current_version": item.version})
    if "parent_id" in changes:
        if changes["parent_id"] == item.id:
            raise ValidationFailed("an item cannot be its own parent")
        _check_parent(db, item.project_id, item.kind, changes["parent_id"])
    allowed = {
        "title",
        "description",
        "status",
        "priority",
        "labels",
        "story_points",
        "uncertainty",
        "parent_id",
    }
    applied = sorted(k for k in changes if k in allowed)
    for k in applied:
        setattr(item, k, changes[k])
    if "story_points" in applied:
        item.estimate = {**(item.estimate or {}), "story_points_set_by": "human"}
    audit.record(
        db,
        action="backlog.item_updated",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=item.project_id,
        target_type="backlog_item",
        target_id=item.id,
        details={"fields": applied},
    )
    events.emit(
        db,
        type="backlog.item_updated",
        project_id=item.project_id,
        payload={"key": item.key, "fields": applied},
    )
    return item


def add_dependency(
    db: Session, principal: Principal, item: BacklogItem, depends_on: BacklogItem, reason: str = ""
) -> BacklogDependency:
    if item.id == depends_on.id:
        raise ValidationFailed("an item cannot depend on itself")
    edges = [
        (a, b)
        for a, b in db.execute(
            select(BacklogDependency.item_id, BacklogDependency.depends_on_id).where(
                BacklogDependency.project_id == item.project_id
            )
        ).all()
    ]
    if (item.id, depends_on.id) in edges:
        raise Conflict("dependency already exists")
    cycle = would_create_cycle(edges, (item.id, depends_on.id))
    if cycle:
        keys = {i.id: i.key for i in db.scalars(select(BacklogItem).where(BacklogItem.id.in_(cycle)))}
        raise Conflict("dependency would create a cycle", details={"cycle": [keys[c] for c in cycle]})
    dep = BacklogDependency(
        project_id=item.project_id,
        item_id=item.id,
        depends_on_id=depends_on.id,
        reason=reason or "added by " + principal.email,
    )
    db.add(dep)
    audit.record(
        db,
        action="backlog.dependency_added",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=item.project_id,
        target_type="backlog_item",
        target_id=item.id,
        details={"depends_on": depends_on.key},
    )
    return dep


def remove_dependency(db: Session, principal: Principal, item: BacklogItem, depends_on_id: uuid.UUID) -> None:
    result = db.execute(
        delete(BacklogDependency).where(
            BacklogDependency.item_id == item.id, BacklogDependency.depends_on_id == depends_on_id
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFound("dependency not found")
    audit.record(
        db,
        action="backlog.dependency_removed",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=item.project_id,
        target_type="backlog_item",
        target_id=item.id,
        details={"depends_on_id": str(depends_on_id)},
    )
