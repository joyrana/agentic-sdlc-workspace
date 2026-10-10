from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin


class SourceRefMixin:
    """Provenance shared by everything extracted from a document."""

    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    locator: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"block_id", "section_path", ...}
    source_text: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(20), default="explicit")  # explicit | inferred | human
    extraction_method: Mapped[str] = mapped_column(String(40), default="heuristic")
    model_ref: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class Requirement(IdMixin, TimestampMixin, SourceRefMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(30))  # functional | non_functional | constraint
    title: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default="should")  # must | should | could
    # draft | approved | superseded
    status: Mapped[str] = mapped_column(String(20), default="draft")
    validation_status: Mapped[str] = mapped_column(String(20), default="unvalidated")
    version: Mapped[int] = mapped_column(Integer, default=1)

    criteria: Mapped[list[AcceptanceCriterion]] = relationship(
        back_populates="requirement", order_by="AcceptanceCriterion.key"
    )

    __mapper_args__ = {"version_id_col": version}


class AcceptanceCriterion(IdMixin, TimestampMixin, SourceRefMixin, Base):
    __tablename__ = "acceptance_criteria"
    __table_args__ = (UniqueConstraint("requirement_id", "key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(60))  # e.g. FR-002-AC1
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    version: Mapped[int] = mapped_column(Integer, default=1)

    requirement: Mapped[Requirement] = relationship(back_populates="criteria")

    __mapper_args__ = {"version_id_col": version}


class BusinessRule(IdMixin, TimestampMixin, SourceRefMixin, Base):
    __tablename__ = "business_rules"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    statement: Mapped[str] = mapped_column(Text)
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class OpenQuestion(IdMixin, TimestampMixin, SourceRefMixin, Base):
    __tablename__ = "open_questions"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # ambiguity | missing_detail | contradiction | assumption | injection_warning
    kind: Mapped[str] = mapped_column(String(30))
    question: Mapped[str] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved | dismissed
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL")
    )


class ArtifactRevision(IdMixin, TimestampMixin, Base):
    """Immutable history of every reviewable artifact (requirements, criteria, proposals, ADRs, plans)."""

    __tablename__ = "artifact_revisions"
    __table_args__ = (UniqueConstraint("artifact_type", "artifact_id", "version"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40))
    artifact_id: Mapped[uuid.UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
