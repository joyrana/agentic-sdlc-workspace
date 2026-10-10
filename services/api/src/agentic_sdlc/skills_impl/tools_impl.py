"""Tool functions. Each enforces project scoping itself; the gateway enforces the allow-list."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from agentic_sdlc.core.errors import NotFound
from agentic_sdlc.orm import DocumentVersion, SourceDocument
from agentic_sdlc.skills.base import SkillContext
from agentic_sdlc.skills.tools import tool


@tool("documents.read")
def documents_read(ctx: SkillContext, document_version_id: str) -> dict[str, Any]:
    """Return the normalized blocks of a document version in the caller's project."""
    row = ctx.db.execute(
        select(DocumentVersion, SourceDocument)
        .join(SourceDocument, SourceDocument.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.id == uuid.UUID(document_version_id),
            SourceDocument.project_id == ctx.execution.project_id,
        )
    ).first()
    normalized = row[0].normalized if row is not None else None
    if row is None or normalized is None:
        raise NotFound("document version not found or not normalized")
    document = row[1]
    return {
        "document_kind": document.kind,
        "title": document.title,
        "blocks": normalized["blocks"],
        "metadata": normalized.get("metadata", {}),
    }
