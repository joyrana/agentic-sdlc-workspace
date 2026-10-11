"""Append-only audit trail. Writes join the caller's transaction so state and audit commit together."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from agentic_sdlc.core.logging import correlation_id_var, redact_obj
from agentic_sdlc.orm import AuditEvent


def record(
    db: Session,
    *,
    action: str,
    actor_id: uuid.UUID | None,
    actor_type: str,
    project_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        project_id=project_id,
        run_id=run_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=redact_obj(details or {}),
        correlation_id=correlation_id_var.get(),
    )
    db.add(event)
    return event
