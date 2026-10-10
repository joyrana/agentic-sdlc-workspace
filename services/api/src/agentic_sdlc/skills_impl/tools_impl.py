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


@tool("repo.read")
def repo_read(ctx: SkillContext, repository_id: str, op: str, path: str | None = None) -> dict[str, Any]:
    """Read-only repository access via git objects at the default branch. Project-scoped."""
    from pathlib import Path

    from agentic_sdlc.core.errors import ValidationFailed
    from agentic_sdlc.execution import repo_reader
    from agentic_sdlc.orm import ProjectRepository

    repo = ctx.db.get(ProjectRepository, uuid.UUID(repository_id))
    if repo is None or repo.project_id != ctx.execution.project_id:
        raise NotFound("repository not found")
    if repo.kind != "fixture":
        raise ValidationFailed("remote git repositories are not supported by this build yet")
    root, rev = Path(repo.location), repo.default_branch
    if op == "tree":
        return {"commit": repo_reader.head_commit(root, rev), "files": repo_reader.list_files(root, rev)}
    if op == "file":
        if path is None:
            raise ValidationFailed("path is required")
        return {"path": path, "content": repo_reader.read_file(root, path, rev)}
    if op == "outline":
        if path is None:
            raise ValidationFailed("path is required")
        source = repo_reader.read_file(root, path, rev)
        return {"path": path, "symbols": repo_reader.python_symbols(source), "source": source}
    raise ValidationFailed(f"unknown op {op}")
