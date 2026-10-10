"""Tool functions. Each enforces project scoping itself; the gateway enforces the allow-list."""

from __future__ import annotations

import uuid
from pathlib import Path
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
def repo_read(
    ctx: SkillContext, repository_id: str, op: str, path: str | None = None, rev: str | None = None
) -> dict[str, Any]:
    """Read-only repository access via git objects (default branch, or a full commit sha). Project-scoped."""
    import re

    from agentic_sdlc.core.errors import ValidationFailed
    from agentic_sdlc.execution import repo_reader
    from agentic_sdlc.orm import ProjectRepository

    repo = ctx.db.get(ProjectRepository, uuid.UUID(repository_id))
    if repo is None or repo.project_id != ctx.execution.project_id:
        raise NotFound("repository not found")
    if repo.kind != "fixture":
        raise ValidationFailed("remote git repositories are not supported by this build yet")
    if rev is not None and not re.match(r"^[0-9a-f]{40}$", rev):
        raise ValidationFailed("rev must be a full commit sha")
    root, rev = Path(repo.location), rev or repo.default_branch
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


def _sample_repo(ctx: SkillContext, repository_id: str) -> Any:
    from agentic_sdlc.core.errors import ValidationFailed
    from agentic_sdlc.orm import ProjectRepository

    repo = ctx.db.get(ProjectRepository, uuid.UUID(repository_id))
    if repo is None or repo.project_id != ctx.execution.project_id:
        raise NotFound("repository not found")
    if repo.kind != "fixture":
        raise ValidationFailed("only fixture repositories are supported by this build")
    return repo


@tool("fixtures.read")
def fixtures_read(ctx: SkillContext, repository_id: str, kind: str, name: str | None = None) -> Any:
    """Sample-only fixture access (scripted coding scenario). Refused for non-sample repositories."""
    from agentic_sdlc.core.errors import PolicyViolation, ValidationFailed
    from agentic_sdlc.execution.coding_agents import load_scenario

    repo = _sample_repo(ctx, repository_id)
    scenario = load_scenario(repo)
    if scenario is None:
        raise PolicyViolation("fixtures are only available for sample repositories with a scenario")
    if kind == "coding_scenario":
        return {k: v for k, v in scenario.items() if k != "_dir"}
    if kind == "coding_patch":
        allowed = {a["patch"] for a in scenario["attempts"]}
        if name is None or name not in allowed:
            raise ValidationFailed("unknown patch")
        return {"name": name, "diff": (Path(scenario["_dir"]) / name).read_text()}
    raise ValidationFailed(f"unknown fixture kind {kind}")


@tool("sandbox.exec")
def sandbox_exec(ctx: SkillContext, repository_id: str, commit: str, suite: str) -> dict[str, Any]:
    """Run a test suite for `commit` in the sandbox. `acceptance`/`regression` add the QA-owned
    acceptance suite when one exists for the repository."""
    import re

    from agentic_sdlc.core.errors import ValidationFailed
    from agentic_sdlc.execution.coding_agents import acceptance_suite_dir
    from agentic_sdlc.execution.test_runner import run_suite

    if suite not in ("developer", "acceptance", "regression", "smoke"):
        raise ValidationFailed("unknown suite")
    if not re.match(r"^[0-9a-f]{40}$", commit):
        raise ValidationFailed("commit must be a full sha")
    repo = _sample_repo(ctx, repository_id)
    acceptance = acceptance_suite_dir(repo) if suite in ("acceptance", "regression") else None
    ctx.check_deadline()
    return run_suite(Path(repo.location), commit, acceptance_dir=acceptance).as_dict()


@tool("evidence.read")
def evidence_read(ctx: SkillContext, kind: str, record_id: str) -> dict[str, Any]:
    """Read recorded evidence (test runs, code changes) belonging to the current workflow run only."""
    from agentic_sdlc.core.errors import ValidationFailed
    from agentic_sdlc.orm import CodeChange, TestRun

    if kind == "test_run":
        tr = ctx.db.get(TestRun, uuid.UUID(record_id))
        if tr is None or tr.run_id != ctx.execution.run_id:
            raise NotFound("evidence not found for this run")
        return {
            "suite": tr.suite,
            "attempt": tr.attempt,
            "status": tr.status,
            "totals": tr.totals,
            "cases": tr.cases,
            "commit": tr.commit,
        }
    if kind == "code_change":
        cc = ctx.db.get(CodeChange, uuid.UUID(record_id))
        if cc is None or cc.run_id != ctx.execution.run_id:
            raise NotFound("evidence not found for this run")
        return {"attempt": cc.attempt, "head_commit": cc.head_commit, "files": cc.files, "status": cc.status}
    raise ValidationFailed("unknown evidence kind")
