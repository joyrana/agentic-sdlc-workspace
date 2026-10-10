"""Human approval gates.

Guarantees (each covered by tests):
- Only human principals holding one of the gate's roles in the project may decide.
- A decision binds to the artifact version and content hash the request was created for; if the
  artifact changed, the request is `stale` and the decision is refused.
- The author of the latest human revision of an artifact cannot approve it (separation of duties).
- Rejections and change requests require a comment.
- Production release approval is refused while production deployment is disabled by policy.
- A request is decided at most once; the workflow resumes from a deduplicated job and re-reads the
  decision from the database rather than trusting the resume payload.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.core.db import utcnow
from agentic_sdlc.core.errors import Conflict, Forbidden, NotFound, PolicyViolation, ValidationFailed
from agentic_sdlc.core.hashing import content_hash
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import (
    ApprovalDecision,
    ApprovalRequest,
    ArchitectureProposal,
    ArtifactRevision,
    ImplementationPlan,
    Release,
)
from agentic_sdlc.workflows import jobs

GATE_ROLES: dict[str, list[str]] = {
    "architecture": ["reviewer", "project_owner"],
    "implementation_scope": ["project_owner"],
    "release_staging": ["release_approver"],
    "release_production": ["release_approver"],
}
SEPARATION_OF_DUTIES = {"architecture", "implementation_scope"}
DECISIONS = {"approved", "rejected", "changes_requested"}
APPROVAL_TTL = timedelta(days=7)


def current_artifact(
    db: Session, artifact_type: str, artifact_id: uuid.UUID
) -> tuple[int, str, uuid.UUID | None]:
    """(version, content hash, author of the latest human revision)."""
    if artifact_type == "architecture_proposal":
        p = db.get(ArchitectureProposal, artifact_id)
        if p is None:
            raise NotFound("proposal not found")
        version, digest = p.version, p.content_hash
    elif artifact_type == "implementation_plan":
        ip = db.get(ImplementationPlan, artifact_id)
        if ip is None:
            raise NotFound("plan not found")
        version, digest = ip.version, ip.content_hash
    elif artifact_type == "release":
        r = db.get(Release, artifact_id)
        if r is None:
            raise NotFound("release not found")
        version, digest = (
            1,
            content_hash({"artifact": r.artifact_sha256, "readiness": r.readiness_hash, "commit": r.commit}),
        )
    else:
        raise ValidationFailed(f"unknown artifact type {artifact_type}")
    author = db.scalar(
        select(ArtifactRevision.author_id).where(
            ArtifactRevision.artifact_type == artifact_type,
            ArtifactRevision.artifact_id == artifact_id,
            ArtifactRevision.version == version,
        )
    )
    return version, digest, author


def ensure_request(
    db: Session,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    gate: str,
    artifact_type: str,
    artifact_id: uuid.UUID,
    requested_action: str,
    policy_checks: list[dict[str, Any]] | None = None,
) -> ApprovalRequest:
    """Idempotent: returns the request for the artifact's *current* version/hash, creating it if needed.

    Earlier pending requests for the same run and gate become stale.
    """
    version, digest, _ = current_artifact(db, artifact_type, artifact_id)
    latest = db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.run_id == run_id,
            ApprovalRequest.gate == gate,
            ApprovalRequest.artifact_id == artifact_id,
            ApprovalRequest.content_hash == digest,
            ApprovalRequest.status != "stale",
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        return latest
    for old in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.run_id == run_id,
            ApprovalRequest.gate == gate,
            ApprovalRequest.status == "pending",
        )
    ):
        old.status = "stale"
    req = ApprovalRequest(
        project_id=project_id,
        run_id=run_id,
        gate=gate,
        requested_action=requested_action,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        artifact_version=version,
        content_hash=digest,
        required_roles=GATE_ROLES[gate],
        status="pending",
        policy_checks=policy_checks or [],
        expires_at=utcnow() + APPROVAL_TTL,
    )
    db.add(req)
    db.flush()
    audit.record(
        db,
        action="approval.requested",
        actor_id=None,
        actor_type="system",
        project_id=project_id,
        run_id=run_id,
        target_type="approval_request",
        target_id=req.id,
        details={
            "gate": gate,
            "artifact_type": artifact_type,
            "artifact_version": version,
            "content_hash": digest,
        },
    )
    events.emit(
        db,
        type="approval.requested",
        project_id=project_id,
        run_id=run_id,
        payload={"request_id": str(req.id), "gate": gate, "required_roles": req.required_roles},
        artifact_refs=[{"type": artifact_type, "id": str(artifact_id), "version": version}],
    )
    return req


def latest_request(db: Session, run_id: uuid.UUID, gate: str) -> ApprovalRequest | None:
    return db.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.run_id == run_id, ApprovalRequest.gate == gate)
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )


def decision_for(db: Session, request_id: uuid.UUID) -> ApprovalDecision | None:
    return db.scalar(select(ApprovalDecision).where(ApprovalDecision.request_id == request_id))


def decide(
    db: Session, principal: Principal, req: ApprovalRequest, *, decision: str, comment: str = ""
) -> ApprovalDecision:
    if decision not in DECISIONS:
        raise ValidationFailed("decision must be approved, rejected or changes_requested")
    if principal.is_service_account:
        raise Forbidden("agents cannot make approval decisions")
    held = principal.roles_in(db, req.project_id) & set(req.required_roles)
    if not held:
        raise Forbidden(
            "you do not hold a role that can decide this approval",
            details={"required_roles": req.required_roles},
        )
    if req.status != "pending":
        raise Conflict(f"approval request is {req.status}", details={"status": req.status})
    if req.expires_at is not None and req.expires_at < utcnow():
        req.status = "stale"
        db.commit()
        raise Conflict("approval request expired; a new request will be issued")
    version, digest, author = current_artifact(db, req.artifact_type, req.artifact_id)
    if digest != req.content_hash:
        req.status = "stale"
        _resume(db, req, f"stale:{req.id}", reason="artifact_changed")
        db.commit()
        raise Conflict(
            "the artifact changed after approval was requested; review the new version",
            details={"requested_version": req.artifact_version, "current_version": version},
        )
    if req.gate in SEPARATION_OF_DUTIES and author is not None and author == principal.user_id:
        raise Forbidden("you authored this revision; another reviewer must decide")
    if decision != "approved" and not comment.strip():
        raise ValidationFailed("a comment is required when rejecting or requesting changes")
    if (
        req.gate == "release_production"
        and decision == "approved"
        and not get_settings().production_deploy_enabled
    ):
        raise PolicyViolation("production deployment is disabled by policy")

    record = ApprovalDecision(
        request_id=req.id,
        approver_id=principal.user_id,
        approver_role=sorted(held)[0],
        decision=decision,
        comment=comment.strip(),
        content_hash_at_decision=digest,
    )
    db.add(record)
    req.status = decision
    req.resulting_transition = {
        "approved": "continue",
        "rejected": "cancel_run",
        "changes_requested": "revise",
    }[decision]
    db.flush()
    audit.record(
        db,
        action=f"approval.{decision}",
        actor_id=principal.user_id,
        actor_type="human",
        project_id=req.project_id,
        run_id=req.run_id,
        target_type="approval_request",
        target_id=req.id,
        details={
            "gate": req.gate,
            "artifact_type": req.artifact_type,
            "artifact_version": req.artifact_version,
            "content_hash": digest,
            "role": record.approver_role,
            "comment": comment[:500],
        },
    )
    events.emit(
        db,
        type="approval.decided",
        project_id=req.project_id,
        run_id=req.run_id,
        payload={"request_id": str(req.id), "gate": req.gate, "decision": decision},
        artifact_refs=[
            {"type": req.artifact_type, "id": str(req.artifact_id), "version": req.artifact_version}
        ],
    )
    _resume(db, req, f"decision:{record.id}", reason="decided")
    return record


def _resume(db: Session, req: ApprovalRequest, dedup: str, *, reason: str) -> None:
    if req.run_id is not None:
        jobs.enqueue(
            db,
            kind="delivery.resume",
            dedup_key=dedup,
            run_id=req.run_id,
            max_attempts=10,
            payload={"run_id": str(req.run_id), "reason": reason, "request_id": str(req.id)},
        )


def invalidate_for_artifact(db: Session, artifact_type: str, artifact_id: uuid.UUID, *, reason: str) -> int:
    """Mark pending requests for an artifact stale and wake the waiting workflow so it re-requests."""
    n = 0
    for req in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.artifact_type == artifact_type,
            ApprovalRequest.artifact_id == artifact_id,
            ApprovalRequest.status == "pending",
        )
    ):
        req.status = "stale"
        n += 1
        events.emit(
            db,
            type="approval.stale",
            project_id=req.project_id,
            run_id=req.run_id,
            payload={"request_id": str(req.id), "gate": req.gate, "reason": reason},
        )
        _resume(db, req, f"stale:{req.id}", reason="artifact_changed")
    return n


def cancel_for_run(db: Session, run_id: uuid.UUID) -> None:
    for req in db.scalars(
        select(ApprovalRequest).where(ApprovalRequest.run_id == run_id, ApprovalRequest.status == "pending")
    ):
        req.status = "cancelled"
