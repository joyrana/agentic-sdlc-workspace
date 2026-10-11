from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from agentic_sdlc.api.deps import DB, CurrentPrincipal, project_permission
from agentic_sdlc.audit import service as audit
from agentic_sdlc.core.errors import Conflict, Forbidden, NotFound, PolicyViolation
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import Deployment, Project, Release
from agentic_sdlc.releases import service as releases

router = APIRouter(tags=["releases"])
Read = Annotated[Project, Depends(project_permission("release.read"))]


class RollbackIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def _dep(d: Deployment) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "environment": d.environment,
        "adapter": d.adapter,
        "status": d.status,
        "approval_request_id": str(d.approval_request_id) if d.approval_request_id else None,
        "started_at": d.started_at.isoformat() if d.started_at else None,
        "finished_at": d.finished_at.isoformat() if d.finished_at else None,
        "smoke": d.smoke,
        "health": d.health,
        "log": d.log,
        "rollback": d.rollback,
    }


def _release(db: DB, r: Release) -> dict[str, Any]:
    deps = db.scalars(
        select(Deployment).where(Deployment.release_id == r.id).order_by(Deployment.created_at)
    ).all()
    return {
        "id": str(r.id),
        "run_id": str(r.run_id),
        "version": r.version_label,
        "commit": r.commit,
        "artifact_sha256": r.artifact_sha256,
        "readiness": r.readiness,
        "readiness_hash": r.readiness_hash,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
        "deployments": [_dep(d) for d in deps],
        "production_deploy_enabled": get_settings().production_deploy_enabled,
    }


@router.get("/projects/{project_id}/releases")
def list_releases(project: Read, db: DB) -> list[dict[str, Any]]:
    return [
        _release(db, r)
        for r in db.scalars(
            select(Release).where(Release.project_id == project.id).order_by(Release.created_at.desc())
        )
    ]


@router.get("/projects/{project_id}/releases/{release_id}")
def get_release(release_id: uuid.UUID, project: Read, db: DB) -> dict[str, Any]:
    r = db.get(Release, release_id)
    if r is None or r.project_id != project.id:
        raise NotFound("release not found")
    return _release(db, r)


@router.post("/projects/{project_id}/releases/{release_id}/rollback")
def manual_rollback(
    release_id: uuid.UUID, body: RollbackIn, project: Read, db: DB, principal: CurrentPrincipal
) -> dict[str, Any]:
    if "release_approver" not in principal.roles_in(db, project.id):
        raise Forbidden("only a release approver can roll back a deployment")
    r = db.get(Release, release_id)
    if r is None or r.project_id != project.id:
        raise NotFound("release not found")
    dep = db.scalar(
        select(Deployment).where(
            Deployment.release_id == r.id,
            Deployment.environment == "staging",
            Deployment.status == "succeeded",
        )
    )
    if dep is None:
        raise Conflict("no active staging deployment for this release")
    releases.rollback(db, dep, f"manual rollback by {principal.email}: {body.reason}")
    audit.record(
        db,
        action="release.manual_rollback",
        actor_id=principal.user_id,
        actor_type="human",
        project_id=project.id,
        target_type="release",
        target_id=r.id,
        details={"reason": body.reason},
    )
    db.commit()
    return _release(db, r)


@router.post("/projects/{project_id}/releases/{release_id}/promote")
def promote_to_production(release_id: uuid.UUID, project: Read, db: DB, principal: CurrentPrincipal) -> None:
    """Production promotion is refused unless explicitly enabled and configured (policy default: off)."""
    if not get_settings().production_deploy_enabled:
        raise PolicyViolation("production deployment is disabled by policy")
    raise PolicyViolation("no production deployment adapter is configured")
