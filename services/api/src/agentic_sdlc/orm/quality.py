"""Code changes, test runs, verification results, defects."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin


class CodeChange(IdMixin, TimestampMixin, Base):
    __tablename__ = "code_changes"
    __table_args__ = (UniqueConstraint("run_id", "backlog_item_id", "attempt"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    backlog_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backlog_items.id", ondelete="CASCADE"))
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_repositories.id", ondelete="CASCADE")
    )
    attempt: Mapped[int] = mapped_column(Integer)
    branch: Mapped[str] = mapped_column(String(200))
    base_commit: Mapped[str] = mapped_column(String(64))
    head_commit: Mapped[str | None] = mapped_column(String(64))
    diff: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list[Any]] = mapped_column(default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    coding_agent: Mapped[dict[str, Any]] = mapped_column(default=dict)  # strategy, model, fixture
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | applied | rejected
    review: Mapped[dict[str, Any]] = mapped_column(default=dict)  # policy/patch review findings
    pull_request_url: Mapped[str | None] = mapped_column(Text)


class TestRun(IdMixin, TimestampMixin, Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_runs"
    __table_args__ = (UniqueConstraint("run_id", "suite", "attempt"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    code_change_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("code_changes.id", ondelete="SET NULL")
    )
    suite: Mapped[str] = mapped_column(String(40))  # developer | acceptance | regression | smoke
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # passed | failed | error
    totals: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cases: Mapped[list[Any]] = mapped_column(default=list)  # [{nodeid, outcome, requirements, message}]
    log: Mapped[str] = mapped_column(Text, default="")  # truncated, redacted
    command: Mapped[list[Any]] = mapped_column(default=list)
    sandbox: Mapped[dict[str, Any]] = mapped_column(default=dict)  # backend, limits, exit code, duration
    commit: Mapped[str | None] = mapped_column(String(64))


class VerificationResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "verification_results"
    __table_args__ = (UniqueConstraint("run_id", "acceptance_criterion_id", "attempt"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    acceptance_criterion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acceptance_criteria.id", ondelete="CASCADE")
    )
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # verified | failed | unverifiable
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    gaps: Mapped[list[Any]] = mapped_column(default=list)


class Defect(IdMixin, TimestampMixin, Base):
    __tablename__ = "defects"
    __table_args__ = (UniqueConstraint("run_id", "dedup_key"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(200))
    backlog_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backlog_items.id", ondelete="SET NULL")
    )
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backlog_items.id", ondelete="SET NULL")
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL")
    )
    acceptance_criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("acceptance_criteria.id", ondelete="SET NULL")
    )
    code_change_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("code_changes.id", ondelete="SET NULL")
    )
    test_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"))
    severity: Mapped[str] = mapped_column(String(10))  # critical | major | minor
    title: Mapped[str] = mapped_column(String(300))
    reproduction_steps: Mapped[list[Any]] = mapped_column(default=list)
    expected: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | fixed | verified | wont_fix
    found_in_attempt: Mapped[int] = mapped_column(Integer)
    fixed_in_attempt: Mapped[int | None] = mapped_column(Integer)
