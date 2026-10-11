from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin


class ArchitectureProposal(IdMixin, TimestampMixin, Base):
    """Current state of a proposal. Every version is also kept in artifact_revisions."""

    __tablename__ = "architecture_proposals"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300))
    # draft | in_review | approved | rejected | changes_requested | superseded
    status: Mapped[str] = mapped_column(String(30), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict[str, Any]] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
    generated_by: Mapped[dict[str, Any]] = mapped_column(default=dict)  # agent/skill/strategy/model
    # `version` is the CONTENT version (incremented only when content changes); approvals bind to it.


class ArchitectureDecisionRecord(IdMixin, TimestampMixin, Base):
    __tablename__ = "adrs"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("architecture_proposals.id", ondelete="SET NULL")
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | accepted | rejected
    content: Mapped[dict[str, Any]] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)


class ImplementationPlan(IdMixin, TimestampMixin, Base):
    __tablename__ = "implementation_plans"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("architecture_proposals.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict[str, Any]] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
