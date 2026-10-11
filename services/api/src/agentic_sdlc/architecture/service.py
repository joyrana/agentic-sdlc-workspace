"""Architecture proposals, ADRs and implementation plans as versioned, content-hashed artifacts."""

from __future__ import annotations

import uuid
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_sdlc.approvals import service as approvals
from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.core.errors import Conflict, NotFound, ValidationFailed
from agentic_sdlc.core.hashing import content_hash
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import ArchitectureDecisionRecord, ArchitectureProposal, ImplementationPlan
from agentic_sdlc.requirements.service import add_revision


def save_proposal(
    db: Session,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    content: dict[str, Any],
    generated_by: dict[str, Any],
    author_id: uuid.UUID | None,
    reason: str,
) -> ArchitectureProposal:
    """Create the run's proposal or add a new version of it."""
    proposal = db.scalar(select(ArchitectureProposal).where(ArchitectureProposal.run_id == run_id))
    digest = content_hash(content)
    if proposal is None:
        proposal = ArchitectureProposal(
            project_id=project_id,
            run_id=run_id,
            title=content["title"],
            status="in_review",
            content=content,
            content_hash=digest,
            generated_by=generated_by,
        )
        db.add(proposal)
        db.flush()
    elif proposal.content_hash == digest:
        return proposal
    else:
        proposal.content = content
        proposal.content_hash = digest
        proposal.title = content["title"]
        proposal.status = "in_review"
        proposal.generated_by = generated_by
        proposal.version += 1
        db.flush()
    add_revision(
        db,
        project_id=project_id,
        artifact_type="architecture_proposal",
        artifact_id=proposal.id,
        version=proposal.version,
        content=content,
        author_id=author_id,
        reason=reason,
    )
    events.emit(
        db,
        type="architecture.proposed",
        project_id=project_id,
        run_id=run_id,
        payload={"version": proposal.version, "recommended": content.get("recommended_option")},
        artifact_refs=[
            {"type": "architecture_proposal", "id": str(proposal.id), "version": proposal.version}
        ],
    )
    return proposal


def save_adr(
    db: Session, *, project_id: uuid.UUID, proposal: ArchitectureProposal, content: dict[str, Any]
) -> ArchitectureDecisionRecord:
    adr = db.scalar(
        select(ArchitectureDecisionRecord).where(ArchitectureDecisionRecord.proposal_id == proposal.id)
    )
    digest = content_hash(content)
    if adr is None:
        number = (
            db.scalar(
                select(func.max(ArchitectureDecisionRecord.number)).where(
                    ArchitectureDecisionRecord.project_id == project_id
                )
            )
            or 0
        ) + 1
        adr = ArchitectureDecisionRecord(
            project_id=project_id,
            proposal_id=proposal.id,
            number=number,
            title=content["title"],
            status="proposed",
            content=content,
            content_hash=digest,
        )
        db.add(adr)
    elif adr.content_hash != digest:
        adr.content, adr.content_hash, adr.title, adr.version = (
            content,
            digest,
            content["title"],
            adr.version + 1,
        )
        adr.status = "proposed"
    db.flush()
    return adr


def edit_proposal(
    db: Session,
    principal: Principal,
    proposal: ArchitectureProposal,
    *,
    expected_version: int,
    content: dict[str, Any],
    output_schema: dict[str, Any],
) -> ArchitectureProposal:
    if proposal.version != expected_version:
        raise Conflict("proposal was modified by someone else", details={"current_version": proposal.version})
    if proposal.status in ("approved", "rejected", "superseded"):
        raise Conflict(f"a {proposal.status} proposal cannot be edited")
    errors = [e.message for e in Draft202012Validator(output_schema).iter_errors(content)][:10]
    if errors:
        raise ValidationFailed("proposal does not match the architecture schema", details={"errors": errors})
    if content["recommended_option"] not in {o["name"] for o in content["options"]}:
        raise ValidationFailed("recommended_option must name one of the options")
    if content_hash(content) == proposal.content_hash:
        return proposal
    proposal.content = content
    proposal.content_hash = content_hash(content)
    proposal.title = content["title"]
    proposal.status = "in_review"
    proposal.version += 1
    db.flush()
    add_revision(
        db,
        project_id=proposal.project_id,
        artifact_type="architecture_proposal",
        artifact_id=proposal.id,
        version=proposal.version,
        content=content,
        author_id=principal.user_id,
        reason="edited by " + principal.email,
    )
    stale = approvals.invalidate_for_artifact(
        db, "architecture_proposal", proposal.id, reason="proposal edited"
    )
    audit.record(
        db,
        action="architecture.edited",
        actor_id=principal.user_id,
        actor_type="human",
        project_id=proposal.project_id,
        run_id=proposal.run_id,
        target_type="architecture_proposal",
        target_id=proposal.id,
        details={"version": proposal.version, "stale_approvals": stale},
    )
    events.emit(
        db,
        type="architecture.edited",
        project_id=proposal.project_id,
        run_id=proposal.run_id,
        payload={"version": proposal.version, "stale_approvals": stale},
        artifact_refs=[
            {"type": "architecture_proposal", "id": str(proposal.id), "version": proposal.version}
        ],
    )
    return proposal


def save_plan(
    db: Session,
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    proposal_id: uuid.UUID,
    content: dict[str, Any],
    author_id: uuid.UUID | None,
    reason: str,
) -> ImplementationPlan:
    plan = db.scalar(select(ImplementationPlan).where(ImplementationPlan.run_id == run_id))
    digest = content_hash(content)
    if plan is None:
        plan = ImplementationPlan(
            project_id=project_id,
            run_id=run_id,
            proposal_id=proposal_id,
            status="in_review",
            content=content,
            content_hash=digest,
        )
        db.add(plan)
        db.flush()
    elif plan.content_hash == digest:
        return plan
    else:
        plan.content, plan.content_hash, plan.status, plan.proposal_id = (
            content,
            digest,
            "in_review",
            proposal_id,
        )
        plan.version += 1
        db.flush()
    add_revision(
        db,
        project_id=project_id,
        artifact_type="implementation_plan",
        artifact_id=plan.id,
        version=plan.version,
        content=content,
        author_id=author_id,
        reason=reason,
    )
    return plan


def edit_plan_scope(
    db: Session,
    principal: Principal,
    plan: ImplementationPlan,
    *,
    expected_version: int,
    remove: list[str],
    reason: str,
) -> ImplementationPlan:
    """Humans may narrow scope (never widen it beyond what was planned and approved architecturally)."""
    if plan.version != expected_version:
        raise Conflict("plan was modified by someone else", details={"current_version": plan.version})
    if plan.status != "in_review":
        raise Conflict(f"a {plan.status} plan cannot be edited")
    content = dict(plan.content)
    unknown = set(remove) - set(content["in_scope"])
    if unknown:
        raise ValidationFailed("items are not in scope", details={"keys": sorted(unknown)})
    if not reason.strip():
        raise ValidationFailed("a reason is required")
    content["in_scope"] = [k for k in content["in_scope"] if k not in remove]
    content["out_of_scope"] = [*content["out_of_scope"], *({"key": k, "reason": reason} for k in remove)]
    content["steps"] = [s for s in content["steps"] if s["backlog_key"] not in remove]
    if not content["in_scope"]:
        raise ValidationFailed("at least one item must stay in scope")
    plan.content = content
    plan.content_hash = content_hash(content)
    plan.version += 1
    db.flush()
    add_revision(
        db,
        project_id=plan.project_id,
        artifact_type="implementation_plan",
        artifact_id=plan.id,
        version=plan.version,
        content=content,
        author_id=principal.user_id,
        reason="scope narrowed by " + principal.email,
    )
    approvals.invalidate_for_artifact(db, "implementation_plan", plan.id, reason="scope changed")
    audit.record(
        db,
        action="plan.scope_narrowed",
        actor_id=principal.user_id,
        actor_type="human",
        project_id=plan.project_id,
        run_id=plan.run_id,
        target_type="implementation_plan",
        target_id=plan.id,
        details={"removed": remove, "reason": reason},
    )
    return plan


def get_proposal(db: Session, project_id: uuid.UUID, proposal_id: uuid.UUID) -> ArchitectureProposal:
    p = db.get(ArchitectureProposal, proposal_id)
    if p is None or p.project_id != project_id:
        raise NotFound("proposal not found")
    return p
