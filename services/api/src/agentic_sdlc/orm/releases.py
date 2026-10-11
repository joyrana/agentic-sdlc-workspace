from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin


class Release(IdMixin, TimestampMixin, Base):
    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("project_id", "version_label"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    version_label: Mapped[str] = mapped_column(String(60))
    commit: Mapped[str] = mapped_column(String(64))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    artifact_key: Mapped[str] = mapped_column(String(64))
    readiness: Mapped[dict[str, Any]] = mapped_column(default=dict)  # report incl. checks
    readiness_hash: Mapped[str] = mapped_column(String(64))
    # candidate | approved | deployed_staging | rejected | rolled_back | blocked
    status: Mapped[str] = mapped_column(String(30), default="candidate")


class Deployment(IdMixin, TimestampMixin, Base):
    __tablename__ = "deployments"

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("releases.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    environment: Mapped[str] = mapped_column(String(20))  # staging | production
    adapter: Mapped[str] = mapped_column(String(40))
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approval_requests.id"))
    status: Mapped[str] = mapped_column(String(20))  # pending | succeeded | failed | blocked | rolled_back
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    smoke: Mapped[dict[str, Any]] = mapped_column(default=dict)
    health: Mapped[dict[str, Any]] = mapped_column(default=dict)
    log: Mapped[str] = mapped_column(Text, default="")
    rollback: Mapped[dict[str, Any]] = mapped_column(default=dict)
