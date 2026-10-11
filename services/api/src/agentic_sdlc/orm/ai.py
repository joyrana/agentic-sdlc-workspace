"""Agents, skills, model registry, token accounting, tool invocations, evaluations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin


class SkillDefinition(IdMixin, TimestampMixin, Base):
    __tablename__ = "skill_definitions"

    skill_id: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    latest_version: Mapped[str] = mapped_column(String(40))


class SkillVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)

    skill_id: Mapped[str] = mapped_column(ForeignKey("skill_definitions.skill_id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[dict[str, Any]] = mapped_column()
    manifest_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | deprecated


class ModelRegistryEntry(IdMixin, TimestampMixin, Base):
    __tablename__ = "model_registry"

    model_id: Mapped[str] = mapped_column(String(120), unique=True)
    entry: Mapped[dict[str, Any]] = mapped_column()  # full registry metadata (see config/models.yaml)
    observed: Mapped[dict[str, Any]] = mapped_column(default=dict)  # success rate, latency, failures


class AgentRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str | None] = mapped_column(String(60))
    agent: Mapped[str] = mapped_column(String(60))
    skill_id: Mapped[str] = mapped_column(String(80))
    skill_version: Mapped[str] = mapped_column(String(40))
    strategy: Mapped[str] = mapped_column(String(20))  # llm | heuristic | scripted | mock
    model_ref: Mapped[str | None] = mapped_column(String(200))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | succeeded | failed
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    routing: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolInvocation(IdMixin, TimestampMixin, Base):
    __tablename__ = "tool_invocations"

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tool: Mapped[str] = mapped_column(String(60))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)  # redacted
    policy_decision: Mapped[str] = mapped_column(String(20))  # allowed | denied
    status: Mapped[str] = mapped_column(String(20))  # ok | error | denied
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)


class TokenUsage(IdMixin, TimestampMixin, Base):
    __tablename__ = "token_usage"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    backlog_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backlog_items.id", ondelete="SET NULL")
    )
    model_id: Mapped[str] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    purpose: Mapped[str] = mapped_column(String(40), default="execution")  # execution | retry | repair


class EvaluationRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"

    suite: Mapped[str] = mapped_column(String(60))
    dataset_version: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)  # skill versions, strategy, model
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    results: Mapped[list[Any]] = mapped_column(default=list)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
