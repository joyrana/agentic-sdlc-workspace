from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin

GATES = (
    "requirements_clarification",
    "architecture",
    "implementation_scope",
    "release_staging",
    "release_production",
)


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    gate: Mapped[str] = mapped_column(String(40))
    requested_action: Mapped[str] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(String(40))
    artifact_id: Mapped[uuid.UUID] = mapped_column()
    artifact_version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    required_roles: Mapped[list[Any]] = mapped_column(default=list)
    # pending | approved | rejected | changes_requested | stale | cancelled
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    policy_checks: Mapped[list[Any]] = mapped_column(default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resulting_transition: Mapped[str | None] = mapped_column(String(60))


class ApprovalDecision(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_decisions"

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"), unique=True
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approver_role: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(30))  # approved | rejected | changes_requested
    comment: Mapped[str] = mapped_column(Text, default="")
    content_hash_at_decision: Mapped[str] = mapped_column(String(64))
