"""Requirements persistence, editing, revisions and change impact.

Extraction output (from any strategy) arrives here already validated. This module is the only place
that writes requirements, criteria, rules and questions.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.core.errors import Conflict, NotFound, ValidationFailed
from agentic_sdlc.core.hashing import content_hash
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    ArtifactRevision,
    BacklogItem,
    BusinessRule,
    DocumentVersion,
    OpenQuestion,
    Requirement,
    SourceDocument,
    backlog_item_requirements,
)
from agentic_sdlc.requirements import rules


def requirement_content(r: Requirement) -> dict[str, Any]:
    return {
        "key": r.key,
        "kind": r.kind,
        "title": r.title,
        "statement": r.statement,
        "priority": r.priority,
        "status": r.status,
    }


def criterion_content(c: AcceptanceCriterion) -> dict[str, Any]:
    return {"key": c.key, "text": c.text, "status": c.status, "origin": c.origin}


def add_revision(
    db: Session,
    *,
    project_id: uuid.UUID,
    artifact_type: str,
    artifact_id: uuid.UUID,
    version: int,
    content: dict[str, Any],
    author_id: uuid.UUID | None,
    reason: str,
) -> ArtifactRevision:
    rev = ArtifactRevision(
        project_id=project_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        version=version,
        content=content,
        content_hash=content_hash(content),
        author_id=author_id,
        reason=reason,
    )
    db.add(rev)
    return rev


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def impacted_backlog(db: Session, requirement_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(BacklogItem.id, BacklogItem.key, BacklogItem.title, BacklogItem.status)
        .join(backlog_item_requirements, backlog_item_requirements.c.backlog_item_id == BacklogItem.id)
        .where(backlog_item_requirements.c.requirement_id == requirement_id)
    ).all()
    return [{"id": str(i), "key": k, "title": t, "status": s} for i, k, t, s in rows]


def persist_extraction(
    db: Session,
    *,
    project_id: uuid.UUID,
    version: DocumentVersion,
    document: SourceDocument,
    extraction: dict[str, Any],
    method: str,
    model_ref: str | None,
    agent_user_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Create or update requirements from one document version. Returns a summary incl. change impact."""
    blocks = {b["block_id"]: b for b in (version.normalized or {}).get("blocks", [])}
    existing = {r.key: r for r in db.scalars(select(Requirement).where(Requirement.project_id == project_id))}
    existing_rules = {
        r.key: r for r in db.scalars(select(BusinessRule).where(BusinessRule.project_id == project_id))
    }
    # Requirements from other documents, for cross-document contradiction checks.
    other_docs: list[dict[str, Any]] = []
    for r in existing.values():
        src_doc = (
            db.scalar(select(DocumentVersion.document_id).where(DocumentVersion.id == r.document_version_id))
            if r.document_version_id
            else None
        )
        if src_doc != document.id:
            other_docs.append(
                {
                    "key": r.key,
                    "statement": r.statement,
                    "block_id": r.locator.get("block_id"),
                    "quote": r.source_text,
                }
            )

    def locator(block_id: str | None) -> dict[str, Any]:
        b = blocks.get(block_id or "")
        if not b:
            return {}
        loc = {
            "block_id": b["block_id"],
            "section_path": b.get("section_path", []),
            "kind": b["kind"],
            "document_id": str(document.id),
            "document_title": document.title,
            "document_version": version.version,
        }
        for k in ("speaker", "timestamp", "table", "row"):
            if b.get(k) is not None:
                loc[k] = b[k]
        return loc

    created, updated, unchanged, renamed = [], [], [], []
    change_impact: list[dict[str, Any]] = []
    key_map: dict[str, Requirement] = {}
    for item in extraction["requirements"]:
        key = item["key"]
        req = existing.get(key)
        same_document = False
        if req is not None and req.document_version_id is not None:
            src = db.get(DocumentVersion, req.document_version_id)
            same_document = src is not None and src.document_id == document.id
        if req is not None and not same_document:
            n = 2
            while f"{key}-D{n}" in existing:
                n += 1
            new_key = f"{key}-D{n}"
            renamed.append({"from": key, "to": new_key})
            db.add(
                OpenQuestion(
                    project_id=project_id,
                    kind="assumption",
                    blocking=False,
                    question=f"Requirement id {key} also appears in another document; this one was stored as "
                    f"{new_key}. Are they the same requirement?",
                    document_version_id=version.id,
                    locator=locator(item.get("block_id")),
                    source_text=item["quote"],
                    extraction_method=method,
                    model_ref=model_ref,
                )
            )
            key, req = new_key, None

        if req is None:
            req = Requirement(
                project_id=project_id,
                key=key,
                kind=item["kind"],
                title=item["title"],
                statement=item["statement"],
                priority=item["priority"],
                origin=item["origin"],
                document_version_id=version.id,
                locator=locator(item.get("block_id")),
                source_text=item["quote"],
                extraction_method=method,
                model_ref=model_ref,
                confidence=item["confidence"],
                validation_status="locator_verified",
            )
            db.add(req)
            db.flush()
            add_revision(
                db,
                project_id=project_id,
                artifact_type="requirement",
                artifact_id=req.id,
                version=1,
                content=requirement_content(req),
                author_id=agent_user_id,
                reason="extracted",
            )
            existing[key] = req
            created.append(key)
        else:
            changed = _norm(req.statement) != _norm(item["statement"])
            req.document_version_id = version.id
            req.locator = locator(item.get("block_id"))
            req.source_text = item["quote"]
            if changed:
                req.statement = item["statement"]
                req.title = item["title"]
                req.priority = item["priority"]
                req.extraction_method = method
                req.model_ref = model_ref
                req.status = "draft"
                req.validation_status = "changed"
                db.flush()
                add_revision(
                    db,
                    project_id=project_id,
                    artifact_type="requirement",
                    artifact_id=req.id,
                    version=req.version,
                    content=requirement_content(req),
                    author_id=agent_user_id,
                    reason=f"source changed in document version {version.version}",
                )
                impact = impacted_backlog(db, req.id)
                change_impact.append({"requirement": key, "impacted_backlog_items": impact})
                updated.append(key)
            else:
                unchanged.append(key)
        key_map[item["key"]] = req

        known = {
            _norm(c.text)
            for c in db.scalars(
                select(AcceptanceCriterion).where(AcceptanceCriterion.requirement_id == req.id)
            )
        }
        next_idx = len(known) + 1
        for c in item["acceptance_criteria"]:
            if _norm(c["text"]) in known:
                continue
            crit = AcceptanceCriterion(
                project_id=project_id,
                requirement_id=req.id,
                key=f"{req.key}-AC{next_idx}",
                text=c["text"],
                origin=c["origin"],
                document_version_id=version.id if c.get("block_id") else None,
                locator=locator(c.get("block_id")),
                source_text=c.get("quote") or "",
                extraction_method=method,
                model_ref=model_ref,
                confidence=item["confidence"] if c["origin"] == "explicit" else min(item["confidence"], 0.5),
            )
            db.add(crit)
            db.flush()
            add_revision(
                db,
                project_id=project_id,
                artifact_type="acceptance_criterion",
                artifact_id=crit.id,
                version=1,
                content=criterion_content(crit),
                author_id=agent_user_id,
                reason="extracted",
            )
            known.add(_norm(c["text"]))
            next_idx += 1

    for item in extraction["business_rules"]:
        req_link = key_map.get(item.get("requirement_key") or "")
        rule = existing_rules.get(item["key"])
        if rule is None:
            db.add(
                BusinessRule(
                    project_id=project_id,
                    key=item["key"],
                    statement=item["statement"],
                    requirement_id=req_link.id if req_link else None,
                    origin=item["origin"],
                    document_version_id=version.id,
                    locator=locator(item["block_id"]),
                    source_text=item["quote"],
                    extraction_method=method,
                    model_ref=model_ref,
                )
            )
        elif _norm(rule.statement) != _norm(item["statement"]):
            rule.statement = item["statement"]
            rule.version += 1
            rule.document_version_id = version.id
            rule.locator = locator(item["block_id"])

    open_existing = {
        (q.kind, _norm(q.question))
        for q in db.scalars(
            select(OpenQuestion).where(OpenQuestion.project_id == project_id, OpenQuestion.status == "open")
        )
    }
    cross = (
        rules.contradictions(
            [{**r, "key": key_map[r["key"]].key} for r in extraction["requirements"]], other_docs
        )
        if other_docs
        else []
    )
    questions = 0
    for q in [*extraction["questions"], *cross]:
        if (q["kind"], _norm(q["question"])) in open_existing:
            continue
        req_link = key_map.get(q.get("requirement_key") or "") or existing.get(q.get("requirement_key") or "")
        db.add(
            OpenQuestion(
                project_id=project_id,
                kind=q["kind"],
                question=q["question"],
                blocking=bool(q["blocking"]),
                requirement_id=req_link.id if req_link else None,
                document_version_id=version.id,
                locator=locator(q.get("block_id")),
                source_text=q.get("quote") or "",
                extraction_method=method,
                model_ref=model_ref,
                origin="inferred",
            )
        )
        open_existing.add((q["kind"], _norm(q["question"])))
        questions += 1

    for ci in change_impact:
        events.emit(
            db,
            type="requirement.changed",
            project_id=project_id,
            payload={
                "requirement": ci["requirement"],
                "impacted_backlog_items": [i["key"] for i in ci["impacted_backlog_items"]],
            },
        )
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "renamed": renamed,
        "business_rules": len(extraction["business_rules"]),
        "questions_added": questions,
        "change_impact": change_impact,
        "method": method,
        "model_ref": model_ref,
    }


# --- human edits ------------------------------------------------------------------------------------


def update_requirement(
    db: Session, principal: Principal, req: Requirement, *, expected_version: int, changes: dict[str, Any]
) -> dict[str, Any]:
    if req.version != expected_version:
        raise Conflict("requirement was modified by someone else", details={"current_version": req.version})
    allowed = {"title", "statement", "priority", "kind", "status"}
    applied = {k: v for k, v in changes.items() if k in allowed and v is not None and getattr(req, k) != v}
    if not applied:
        return {"changed": False, "impacted_backlog_items": []}
    if "status" in applied and applied["status"] not in ("draft", "approved"):
        raise ValidationFailed("status must be draft or approved")
    for k, v in applied.items():
        setattr(req, k, v)
    if "statement" in applied:
        req.origin = "human"
        req.validation_status = "human_edited"
    db.flush()
    add_revision(
        db,
        project_id=req.project_id,
        artifact_type="requirement",
        artifact_id=req.id,
        version=req.version,
        content=requirement_content(req),
        author_id=principal.user_id,
        reason="edited by " + principal.email,
    )
    impact = impacted_backlog(db, req.id) if "statement" in applied else []
    audit.record(
        db,
        action="requirement.updated",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=req.project_id,
        target_type="requirement",
        target_id=req.id,
        details={"fields": sorted(applied), "version": req.version},
    )
    events.emit(
        db,
        type="requirement.updated",
        project_id=req.project_id,
        payload={
            "key": req.key,
            "fields": sorted(applied),
            "impacted_backlog_items": [i["key"] for i in impact],
        },
        artifact_refs=[{"type": "requirement", "id": str(req.id), "version": req.version}],
    )
    return {"changed": True, "impacted_backlog_items": impact}


def add_criterion(db: Session, principal: Principal, req: Requirement, *, text: str) -> AcceptanceCriterion:
    if not text.strip():
        raise ValidationFailed("criterion text is required")
    count = len(
        list(db.scalars(select(AcceptanceCriterion.id).where(AcceptanceCriterion.requirement_id == req.id)))
    )
    crit = AcceptanceCriterion(
        project_id=req.project_id,
        requirement_id=req.id,
        key=f"{req.key}-AC{count + 1}",
        text=text.strip(),
        origin="human",
        extraction_method="human",
        confidence=1.0,
    )
    db.add(crit)
    db.flush()
    add_revision(
        db,
        project_id=req.project_id,
        artifact_type="acceptance_criterion",
        artifact_id=crit.id,
        version=1,
        content=criterion_content(crit),
        author_id=principal.user_id,
        reason="added",
    )
    audit.record(
        db,
        action="criterion.added",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=req.project_id,
        target_type="acceptance_criterion",
        target_id=crit.id,
        details={"key": crit.key},
    )
    events.emit(
        db,
        type="criterion.added",
        project_id=req.project_id,
        payload={"key": crit.key},
        artifact_refs=[{"type": "acceptance_criterion", "id": str(crit.id), "version": 1}],
    )
    return crit


def update_criterion(
    db: Session,
    principal: Principal,
    crit: AcceptanceCriterion,
    *,
    expected_version: int,
    text: str | None = None,
    status: str | None = None,
) -> AcceptanceCriterion:
    if crit.version != expected_version:
        raise Conflict("criterion was modified by someone else", details={"current_version": crit.version})
    if status is not None and status not in ("draft", "approved", "rejected"):
        raise ValidationFailed("status must be draft, approved or rejected")
    changed = []
    if text is not None and text.strip() and text.strip() != crit.text:
        crit.text = text.strip()
        crit.origin = "human"
        crit.extraction_method = "human"
        changed.append("text")
    if status is not None and status != crit.status:
        crit.status = status
        changed.append("status")
    if not changed:
        return crit
    db.flush()
    add_revision(
        db,
        project_id=crit.project_id,
        artifact_type="acceptance_criterion",
        artifact_id=crit.id,
        version=crit.version,
        content=criterion_content(crit),
        author_id=principal.user_id,
        reason="edited by " + principal.email,
    )
    audit.record(
        db,
        action="criterion.updated",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=crit.project_id,
        target_type="acceptance_criterion",
        target_id=crit.id,
        details={"fields": changed, "version": crit.version},
    )
    events.emit(
        db,
        type="criterion.updated",
        project_id=crit.project_id,
        payload={"key": crit.key, "fields": changed},
        artifact_refs=[{"type": "acceptance_criterion", "id": str(crit.id), "version": crit.version}],
    )
    return crit


def resolve_question(
    db: Session, principal: Principal, q: OpenQuestion, *, resolution: str, status: str = "resolved"
) -> OpenQuestion:
    if q.status != "open":
        raise Conflict("question is already closed")
    if status not in ("resolved", "dismissed"):
        raise ValidationFailed("status must be resolved or dismissed")
    if not resolution.strip():
        raise ValidationFailed("a resolution note is required")
    q.status = status
    q.resolution = resolution.strip()
    q.resolved_by = principal.user_id
    audit.record(
        db,
        action=f"question.{status}",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=q.project_id,
        target_type="open_question",
        target_id=q.id,
        details={"kind": q.kind, "blocking": q.blocking},
    )
    events.emit(
        db,
        type="question.resolved",
        project_id=q.project_id,
        payload={"question_id": str(q.id), "blocking": q.blocking, "status": status},
    )
    return q


def get_requirement(db: Session, project_id: uuid.UUID, requirement_id: uuid.UUID) -> Requirement:
    req = db.get(Requirement, requirement_id)
    if req is None or req.project_id != project_id:
        raise NotFound("requirement not found")
    return req
