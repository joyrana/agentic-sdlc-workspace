"""Standalone backlog planning job (outside a delivery run)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from agentic_sdlc.backlog.service import generate_backlog, requirements_snapshot
from agentic_sdlc.core.db import session_scope
from agentic_sdlc.orm import Project, ProjectRepository


def handle(payload: dict[str, Any], run_id: uuid.UUID | None) -> None:
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(payload["project_id"]))
        assert project is not None
        repo = db.scalar(
            select(ProjectRepository)
            .where(ProjectRepository.project_id == project.id)
            .order_by(ProjectRepository.created_at)
            .limit(1)
        )
        generate_backlog(
            db, project, run_id=None, snapshot=requirements_snapshot(db, project.id), repository=repo
        )
