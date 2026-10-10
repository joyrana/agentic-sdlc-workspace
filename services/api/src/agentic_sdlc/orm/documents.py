from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentic_sdlc.core.db import Base, IdMixin, TimestampMixin


class SourceDocument(IdMixin, TimestampMixin, Base):
    __tablename__ = "source_documents"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20))  # brd | transcript | spec
    media_type: Mapped[str] = mapped_column(String(120))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    latest_version: Mapped[int] = mapped_column(Integer, default=0)

    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", order_by="DocumentVersion.version"
    )


class DocumentVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(300))  # display only; never used as a path
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(64))
    # pending | processing | processed | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    normalized: Mapped[dict[str, Any] | None] = mapped_column()  # {"blocks": [...], "metadata": {...}}
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    extraction_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)

    document: Mapped[SourceDocument] = relationship(back_populates="versions")


class DocumentBlock(IdMixin, Base):
    """Normalized block for full-text search and stable passage references."""

    __tablename__ = "document_blocks"
    __table_args__ = (UniqueConstraint("document_version_id", "block_id"),)

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    block_id: Mapped[str] = mapped_column(String(40))  # e.g. "b0042"
    kind: Mapped[str] = mapped_column(String(20))  # heading | paragraph | list_item | table_row | utterance
    ordinal: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[list[Any]] = mapped_column(default=list)
    text: Mapped[str] = mapped_column(Text)
