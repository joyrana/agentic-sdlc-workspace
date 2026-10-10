from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func, select

from agentic_sdlc.api.deps import DB, CurrentPrincipal, project_permission
from agentic_sdlc.api.schemas_requirements import (
    BlockOut,
    BusinessRuleOut,
    CriterionCreate,
    CriterionOut,
    CriterionUpdate,
    DocumentDetail,
    DocumentOut,
    DocumentVersionOut,
    QuestionOut,
    QuestionResolve,
    RequirementDetail,
    RequirementOut,
    RequirementUpdate,
    RequirementUpdateOut,
    RevisionOut,
    UploadOut,
)
from agentic_sdlc.core.errors import NotFound, ValidationFailed
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.documents import service as documents
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    ArtifactRevision,
    BusinessRule,
    DocumentBlock,
    DocumentVersion,
    OpenQuestion,
    Project,
    Requirement,
    SourceDocument,
)
from agentic_sdlc.requirements import service as reqs

router = APIRouter(tags=["requirements"])

Read = Annotated[Project, Depends(project_permission("requirement.read"))]
Edit = Annotated[Project, Depends(project_permission("requirement.edit"))]


# --- documents ----------------------------------------------------------------------------------------


@router.post("/projects/{project_id}/documents", response_model=UploadOut, status_code=202)
async def upload_document(
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("document.upload"))],
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["brd", "transcript", "spec"], Form()],
    title: Annotated[str | None, Form()] = None,
    document_id: Annotated[uuid.UUID | None, Form()] = None,
) -> UploadOut:
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed("file is too large", details={"limit_bytes": limit})
    doc, version = documents.upload(
        db,
        principal,
        project,
        filename=file.filename or "upload",
        data=data,
        kind=kind,
        title=title,
        document_id=document_id,
    )
    db.commit()
    return UploadOut(
        document=DocumentOut.model_validate(doc), version=DocumentVersionOut.model_validate(version)
    )


@router.get("/projects/{project_id}/documents", response_model=list[DocumentDetail])
def list_documents(
    db: DB, project: Annotated[Project, Depends(project_permission("document.read"))]
) -> list[SourceDocument]:
    return list(
        db.scalars(
            select(SourceDocument)
            .where(SourceDocument.project_id == project.id)
            .order_by(SourceDocument.created_at.desc())
        )
    )


@router.get("/projects/{project_id}/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: uuid.UUID, db: DB, project: Annotated[Project, Depends(project_permission("document.read"))]
) -> SourceDocument:
    doc = db.get(SourceDocument, document_id)
    if doc is None or doc.project_id != project.id:
        raise NotFound("document not found")
    return doc


@router.get(
    "/projects/{project_id}/documents/{document_id}/versions/{version}/blocks", response_model=list[BlockOut]
)
def document_blocks(
    document_id: uuid.UUID,
    version: int,
    db: DB,
    project: Annotated[Project, Depends(project_permission("document.read"))],
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[DocumentBlock]:
    dv = db.scalar(
        select(DocumentVersion)
        .join(SourceDocument, SourceDocument.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version == version,
            SourceDocument.project_id == project.id,
        )
    )
    if dv is None:
        raise NotFound("document version not found")
    stmt = select(DocumentBlock).where(DocumentBlock.document_version_id == dv.id)
    if q:
        stmt = stmt.where(
            func.to_tsvector("english", DocumentBlock.text).op("@@")(func.websearch_to_tsquery("english", q))
        )
    return list(db.scalars(stmt.order_by(DocumentBlock.ordinal)))


# --- requirements -------------------------------------------------------------------------------------


def _question_counts(db: DB, project_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(OpenQuestion.requirement_id, func.count())
        .where(OpenQuestion.project_id == project_id, OpenQuestion.status == "open")
        .group_by(OpenQuestion.requirement_id)
    ).all()
    return {r: n for r, n in rows if r is not None}


@router.get("/projects/{project_id}/requirements", response_model=list[RequirementOut])
def list_requirements(project: Read, db: DB) -> list[RequirementOut]:
    counts = _question_counts(db, project.id)
    out = []
    for r in db.scalars(
        select(Requirement).where(Requirement.project_id == project.id).order_by(Requirement.key)
    ):
        item = RequirementOut.model_validate(r)
        item.open_questions = counts.get(r.id, 0)
        out.append(item)
    return out


@router.get("/projects/{project_id}/requirements/{requirement_id}", response_model=RequirementDetail)
def get_requirement(requirement_id: uuid.UUID, project: Read, db: DB) -> RequirementDetail:
    r = reqs.get_requirement(db, project.id, requirement_id)
    revisions = db.scalars(
        select(ArtifactRevision)
        .where(ArtifactRevision.artifact_type == "requirement", ArtifactRevision.artifact_id == r.id)
        .order_by(ArtifactRevision.version.desc())
    ).all()
    base = RequirementOut.model_validate(r).model_dump()
    base["open_questions"] = _question_counts(db, project.id).get(r.id, 0)
    return RequirementDetail(
        **base,
        revisions=[RevisionOut.model_validate(x) for x in revisions],
        backlog_items=reqs.impacted_backlog(db, r.id),
    )


@router.patch("/projects/{project_id}/requirements/{requirement_id}", response_model=RequirementUpdateOut)
def update_requirement(
    requirement_id: uuid.UUID, body: RequirementUpdate, project: Edit, db: DB, principal: CurrentPrincipal
) -> RequirementUpdateOut:
    r = reqs.get_requirement(db, project.id, requirement_id)
    result = reqs.update_requirement(
        db,
        principal,
        r,
        expected_version=body.expected_version,
        changes=body.model_dump(exclude={"expected_version"}, exclude_none=True),
    )
    db.commit()
    db.refresh(r)
    return RequirementUpdateOut(
        requirement=RequirementOut.model_validate(r), impacted_backlog_items=result["impacted_backlog_items"]
    )


@router.post(
    "/projects/{project_id}/requirements/{requirement_id}/criteria",
    response_model=CriterionOut,
    status_code=201,
)
def add_criterion(
    requirement_id: uuid.UUID, body: CriterionCreate, project: Edit, db: DB, principal: CurrentPrincipal
) -> AcceptanceCriterion:
    r = reqs.get_requirement(db, project.id, requirement_id)
    crit = reqs.add_criterion(db, principal, r, text=body.text)
    db.commit()
    return crit


@router.patch("/projects/{project_id}/acceptance-criteria/{criterion_id}", response_model=CriterionOut)
def update_criterion(
    criterion_id: uuid.UUID, body: CriterionUpdate, project: Edit, db: DB, principal: CurrentPrincipal
) -> AcceptanceCriterion:
    crit = db.get(AcceptanceCriterion, criterion_id)
    if crit is None or crit.project_id != project.id:
        raise NotFound("criterion not found")
    reqs.update_criterion(
        db, principal, crit, expected_version=body.expected_version, text=body.text, status=body.status
    )
    db.commit()
    db.refresh(crit)
    return crit


@router.get(
    "/projects/{project_id}/acceptance-criteria/{criterion_id}/revisions", response_model=list[RevisionOut]
)
def criterion_revisions(criterion_id: uuid.UUID, project: Read, db: DB) -> list[ArtifactRevision]:
    crit = db.get(AcceptanceCriterion, criterion_id)
    if crit is None or crit.project_id != project.id:
        raise NotFound("criterion not found")
    return list(
        db.scalars(
            select(ArtifactRevision)
            .where(
                ArtifactRevision.artifact_type == "acceptance_criterion",
                ArtifactRevision.artifact_id == crit.id,
            )
            .order_by(ArtifactRevision.version.desc())
        )
    )


@router.get("/projects/{project_id}/business-rules", response_model=list[BusinessRuleOut])
def list_rules(project: Read, db: DB) -> list[BusinessRule]:
    return list(
        db.scalars(
            select(BusinessRule).where(BusinessRule.project_id == project.id).order_by(BusinessRule.key)
        )
    )


@router.get("/projects/{project_id}/questions", response_model=list[QuestionOut])
def list_questions(
    project: Read,
    db: DB,
    status: Annotated[Literal["open", "resolved", "dismissed", "all"], Query()] = "open",
) -> list[OpenQuestion]:
    stmt = select(OpenQuestion).where(OpenQuestion.project_id == project.id)
    if status != "all":
        stmt = stmt.where(OpenQuestion.status == status)
    return list(db.scalars(stmt.order_by(OpenQuestion.blocking.desc(), OpenQuestion.created_at)))


@router.post("/projects/{project_id}/questions/{question_id}/resolve", response_model=QuestionOut)
def resolve_question(
    question_id: uuid.UUID,
    body: QuestionResolve,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("question.resolve"))],
) -> OpenQuestion:
    q = db.get(OpenQuestion, question_id)
    if q is None or q.project_id != project.id:
        raise NotFound("question not found")
    reqs.resolve_question(db, principal, q, resolution=body.resolution, status=body.status)
    db.commit()
    return q
