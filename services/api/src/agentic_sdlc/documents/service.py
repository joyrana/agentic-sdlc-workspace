"""Document upload and versioning."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.core.errors import NotFound, ValidationFailed
from agentic_sdlc.core.logging import correlation_id_var
from agentic_sdlc.core.storage import get_storage
from agentic_sdlc.documents.validation import validate_upload
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import DocumentVersion, Project, SourceDocument
from agentic_sdlc.workflows import jobs

DocumentKind = Literal["brd", "transcript", "spec"]


def upload(
    db: Session,
    principal: Principal,
    project: Project,
    *,
    filename: str,
    data: bytes,
    kind: DocumentKind,
    title: str | None = None,
    document_id: uuid.UUID | None = None,
    is_sample: bool = False,
) -> tuple[SourceDocument, DocumentVersion]:
    checked = validate_upload(filename, data)
    if checked.format == "text" and kind == "brd" and not filename.lower().endswith(".md"):
        pass  # plain-text BRDs are allowed
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:300] or "upload"
    key = get_storage().put(data)

    if document_id is not None:
        doc = db.get(SourceDocument, document_id)
        if doc is None or doc.project_id != project.id:
            raise NotFound("document not found")
        if doc.kind != kind:
            raise ValidationFailed("a new version must have the same document kind")
        latest = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.latest_version
            )
        )
        if latest is not None and latest.sha256 == key:
            raise ValidationFailed("this file is identical to the current version")
    else:
        doc = SourceDocument(
            project_id=project.id,
            title=(title or safe_name.rsplit(".", 1)[0])[:300],
            kind=kind,
            media_type=checked.media_type,
            created_by=principal.user_id,
            is_sample=is_sample,
        )
        db.add(doc)
        db.flush()

    doc.latest_version += 1
    version = DocumentVersion(
        document_id=doc.id,
        version=doc.latest_version,
        original_filename=safe_name,
        sha256=key,
        size_bytes=len(data),
        storage_key=key,
        status="pending",
        warnings=list(checked.warnings),
    )
    db.add(version)
    db.flush()
    jobs.enqueue(
        db,
        kind="intake",
        dedup_key=f"intake:{version.id}",
        payload={
            "document_version_id": str(version.id),
            "project_id": str(project.id),
            "correlation_id": correlation_id_var.get(),
        },
    )
    audit.record(
        db,
        action="document.uploaded",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="document_version",
        target_id=version.id,
        details={
            "document_id": str(doc.id),
            "version": version.version,
            "kind": kind,
            "sha256": key,
            "size_bytes": len(data),
        },
    )
    events.emit(
        db,
        type="document.uploaded",
        project_id=project.id,
        payload={"document_id": str(doc.id), "version": version.version, "title": doc.title},
        artifact_refs=[{"type": "document_version", "id": str(version.id), "version": version.version}],
    )
    return doc, version
