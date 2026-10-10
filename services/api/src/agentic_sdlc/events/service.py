"""Typed event envelope and persistence. Events are written in the producing transaction (outbox)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.core.logging import correlation_id_var, redact_obj
from agentic_sdlc.core.tracing import current_trace_context
from agentic_sdlc.orm import WorkflowEvent

SCHEMA_VERSION = 1


class ArtifactRef(BaseModel):
    type: str
    id: str
    version: int | None = None


class EventEnvelope(BaseModel):
    event_id: uuid.UUID
    seq: int
    type: str
    schema_version: int
    project_id: uuid.UUID
    run_id: uuid.UUID | None
    ts: datetime
    correlation_id: str | None
    causation_id: str | None
    artifact_refs: list[ArtifactRef]
    payload: dict[str, Any]
    trace_context: dict[str, Any] | None = None


def emit(
    db: Session,
    *,
    type: str,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    causation_id: str | None = None,
) -> WorkflowEvent:
    """Persist an event. Payloads are redacted; never put document bodies or prompts in them."""
    ev = WorkflowEvent(
        event_id=uuid.uuid4(),
        type=type,
        schema_version=SCHEMA_VERSION,
        project_id=project_id,
        run_id=run_id,
        correlation_id=correlation_id_var.get(),
        causation_id=causation_id,
        artifact_refs=artifact_refs or [],
        payload=redact_obj(payload or {}),
        trace_context=current_trace_context(),
    )
    db.add(ev)
    return ev


def to_envelope(ev: WorkflowEvent) -> EventEnvelope:
    return EventEnvelope(
        event_id=ev.event_id,
        seq=ev.seq,
        type=ev.type,
        schema_version=ev.schema_version,
        project_id=ev.project_id,
        run_id=ev.run_id,
        ts=ev.ts,
        correlation_id=ev.correlation_id,
        causation_id=ev.causation_id,
        artifact_refs=[ArtifactRef(**r) for r in ev.artifact_refs],
        payload=ev.payload,
        trace_context=ev.trace_context,
    )


def events_after(
    db: Session, project_id: uuid.UUID, after_seq: int, *, run_id: uuid.UUID | None = None, limit: int = 200
) -> list[WorkflowEvent]:
    stmt = select(WorkflowEvent).where(WorkflowEvent.project_id == project_id, WorkflowEvent.seq > after_seq)
    if run_id is not None:
        stmt = stmt.where(WorkflowEvent.run_id == run_id)
    return list(db.scalars(stmt.order_by(WorkflowEvent.seq).limit(limit)))
