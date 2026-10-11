"""Contracts for documents and requirements endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_sdlc.api.schemas import ORMModel


class DocumentVersionOut(ORMModel):
    id: uuid.UUID
    version: int
    original_filename: str
    sha256: str
    size_bytes: int
    status: str
    warnings: list[Any]
    extraction_summary: dict[str, Any]
    created_at: datetime


class DocumentOut(ORMModel):
    id: uuid.UUID
    title: str
    kind: str
    media_type: str
    is_sample: bool
    latest_version: int
    created_at: datetime


class DocumentDetail(DocumentOut):
    versions: list[DocumentVersionOut]


class UploadOut(BaseModel):
    document: DocumentOut
    version: DocumentVersionOut


class BlockOut(BaseModel):
    block_id: str
    kind: str
    ordinal: int
    section_path: list[Any]
    text: str


class SourceRef(BaseModel):
    document_version_id: uuid.UUID | None
    locator: dict[str, Any]
    source_text: str
    origin: str
    extraction_method: str
    model_ref: str | None
    confidence: float


class CriterionOut(ORMModel):
    id: uuid.UUID
    key: str
    text: str
    status: str
    version: int
    origin: str
    extraction_method: str
    model_ref: str | None
    confidence: float
    locator: dict[str, Any]
    source_text: str
    updated_at: datetime


class RequirementOut(ORMModel):
    id: uuid.UUID
    key: str
    kind: str
    title: str
    statement: str
    priority: str
    status: str
    validation_status: str
    version: int
    origin: str
    extraction_method: str
    model_ref: str | None
    confidence: float
    locator: dict[str, Any]
    source_text: str
    document_version_id: uuid.UUID | None
    criteria: list[CriterionOut]
    open_questions: int = 0
    updated_at: datetime


class RevisionOut(ORMModel):
    version: int
    content: dict[str, Any]
    content_hash: str
    author_id: uuid.UUID | None
    reason: str
    created_at: datetime


class RequirementDetail(RequirementOut):
    revisions: list[RevisionOut]
    backlog_items: list[dict[str, Any]]


class RequirementUpdate(BaseModel):
    expected_version: int
    title: str | None = Field(default=None, min_length=1, max_length=300)
    statement: str | None = Field(default=None, min_length=1)
    priority: Literal["must", "should", "could"] | None = None
    kind: Literal["functional", "non_functional", "constraint"] | None = None
    status: Literal["draft", "approved"] | None = None


class RequirementUpdateOut(BaseModel):
    requirement: RequirementOut
    impacted_backlog_items: list[dict[str, Any]]


class CriterionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class CriterionUpdate(BaseModel):
    expected_version: int
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["draft", "approved", "rejected"] | None = None


class BusinessRuleOut(ORMModel):
    id: uuid.UUID
    key: str
    statement: str
    requirement_id: uuid.UUID | None
    origin: str
    extraction_method: str
    locator: dict[str, Any]
    source_text: str


class QuestionOut(ORMModel):
    id: uuid.UUID
    kind: str
    question: str
    blocking: bool
    status: str
    resolution: str | None
    requirement_id: uuid.UUID | None
    locator: dict[str, Any]
    source_text: str
    extraction_method: str
    created_at: datetime


class QuestionResolve(BaseModel):
    resolution: str = Field(min_length=1, max_length=4000)
    status: Literal["resolved", "dismissed"] = "resolved"
