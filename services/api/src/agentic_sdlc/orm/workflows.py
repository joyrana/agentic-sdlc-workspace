from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin, utcnow

RUN_STATUSES = (
    "queued",
    "running",
    "awaiting_approval",
    "completed",
    "escalated",
    "cancelled",
    "failed",
)


class WorkflowRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    definition: Mapped[str] = mapped_column(String(40))  # delivery | intake
    definition_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(60))
    thread_id: Mapped[str] = mapped_column(String(80), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)  # limits, repository, options
    input_artifacts: Mapped[dict[str, Any]] = mapped_column(default=dict)  # artifact id -> version/hash
    counters: Mapped[dict[str, Any]] = mapped_column(default=dict)  # model_calls, tokens, cost, repairs
    outcome: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class WorkflowJob(IdMixin, TimestampMixin, Base):
    """Durable work queue claimed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "workflow_jobs"

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))  # start | resume | intake
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", index=True
    )  # queued|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)


class WorkflowEvent(Base):
    """Append-only event log; `seq` is the SSE Last-Event-ID."""

    __tablename__ = "workflow_events"

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    artifact_refs: Mapped[list[Any]] = mapped_column(default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace_context: Mapped[dict[str, Any] | None] = mapped_column()
