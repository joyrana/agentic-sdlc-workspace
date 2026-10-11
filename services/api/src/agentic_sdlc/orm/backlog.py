from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin

BACKLOG_KINDS = ("epic", "feature", "story", "task", "bug", "enhancement")

backlog_item_requirements = Table(
    "backlog_item_requirements",
    Base.metadata,
    Column("backlog_item_id", ForeignKey("backlog_items.id", ondelete="CASCADE"), primary_key=True),
    Column("requirement_id", ForeignKey("requirements.id", ondelete="CASCADE"), primary_key=True),
)

backlog_item_criteria = Table(
    "backlog_item_criteria",
    Base.metadata,
    Column("backlog_item_id", ForeignKey("backlog_items.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "acceptance_criterion_id", ForeignKey("acceptance_criteria.id", ondelete="CASCADE"), primary_key=True
    ),
)


class BacklogItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "backlog_items"
    __table_args__ = (
        UniqueConstraint("project_id", "key"),
        CheckConstraint("kind in ('epic','feature','story','task','bug','enhancement')", name="kind"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backlog_items.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    key: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo | in_progress | done | blocked
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 highest
    labels: Mapped[list[Any]] = mapped_column(default=list)
    origin: Mapped[str] = mapped_column(String(20), default="generated")  # generated | human
    # Estimates are deliberately separate dimensions (story points never derived from tokens)
    story_points: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[str | None] = mapped_column(String(10))  # low | medium | high
    estimate: Mapped[dict[str, Any]] = mapped_column(default=dict)  # tokens/cost/duration + method
    actuals: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model_route: Mapped[dict[str, Any]] = mapped_column(default=dict)
    assigned_agent: Mapped[str | None] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}


class BacklogDependency(IdMixin, TimestampMixin, Base):
    """`item_id` cannot start until `depends_on_id` is done."""

    __tablename__ = "backlog_dependencies"
    __table_args__ = (
        UniqueConstraint("item_id", "depends_on_id"),
        CheckConstraint("item_id <> depends_on_id", name="no_self_dependency"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backlog_items.id", ondelete="CASCADE"), index=True)
    depends_on_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backlog_items.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
