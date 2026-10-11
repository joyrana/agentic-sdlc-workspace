from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from agentic_sdlc.api.deps import DB, CurrentPrincipal, project_permission
from agentic_sdlc.api.schemas import (
    AuditOut,
    EventOut,
    MemberAdd,
    MemberOut,
    Page,
    ProjectCreate,
    ProjectOut,
    ProjectSummary,
    ProjectUpdate,
    RepositoryCreate,
    RepositoryOut,
)
from agentic_sdlc.auth.service import authorize
from agentic_sdlc.core.db import SessionLocal
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import AuditEvent, Project, ProjectRepository
from agentic_sdlc.projects import service
from agentic_sdlc.projects.summary import project_summary

router = APIRouter(tags=["projects"])

ReadProject = Annotated[Project, Depends(project_permission("project.read"))]


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: DB, principal: CurrentPrincipal) -> Project:
    project = service.create_project(
        db,
        principal,
        name=body.name,
        key=body.key,
        description=body.description,
        goals=body.goals,
        constraints=body.constraints,
    )
    db.commit()
    return project


@router.get("/projects", response_model=Page[ProjectOut])
def list_projects(
    db: DB, principal: CurrentPrincipal, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> Page[ProjectOut]:
    projects = service.list_projects(db, principal)
    return Page[ProjectOut](
        items=[ProjectOut.model_validate(p) for p in projects[offset : offset + limit]],
        total=len(projects),
        limit=limit,
        offset=offset,
    )


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project: ReadProject) -> Project:
    return project


@router.get("/projects/{project_id}/summary", response_model=ProjectSummary)
def get_summary(project: ReadProject, db: DB, principal: CurrentPrincipal) -> ProjectSummary:
    return project_summary(db, project, sorted(principal.roles_in(db, project.id)))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    body: ProjectUpdate,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("project.update"))],
) -> Project:
    service.update_project(
        db,
        principal,
        project,
        expected_version=body.expected_version,
        changes=body.model_dump(exclude={"expected_version"}, exclude_none=True),
    )
    db.commit()
    return project


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def list_members(project: ReadProject, db: DB) -> list[MemberOut]:
    return [
        MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role)
        for m, u in service.list_members(db, project.id)
    ]


@router.post("/projects/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(
    body: MemberAdd,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("project.members.manage"))],
) -> MemberOut:
    m = service.add_member(db, principal, project, email=body.email, role=body.role)
    db.commit()
    return MemberOut(user_id=m.user.id, email=m.user.email, display_name=m.user.display_name, role=m.role)


@router.delete("/projects/{project_id}/members/{user_id}/{role}", status_code=204)
def remove_member(
    user_id: uuid.UUID,
    role: str,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("project.members.manage"))],
) -> None:
    service.remove_member(db, principal, project, user_id=user_id, role=role)
    db.commit()


@router.get("/projects/{project_id}/repositories", response_model=list[RepositoryOut])
def list_repositories(project: ReadProject, db: DB) -> list[ProjectRepository]:
    return list(db.scalars(select(ProjectRepository).where(ProjectRepository.project_id == project.id)))


@router.post("/projects/{project_id}/repositories", response_model=RepositoryOut, status_code=201)
def add_repository(
    body: RepositoryCreate,
    db: DB,
    principal: CurrentPrincipal,
    project: Annotated[Project, Depends(project_permission("repository.manage"))],
) -> ProjectRepository:
    repo = service.add_repository(
        db,
        principal,
        project,
        name=body.name,
        kind=body.kind,
        location=body.location,
        default_branch=body.default_branch,
    )
    db.commit()
    return repo


@router.get("/projects/{project_id}/audit", response_model=Page[AuditOut])
def audit_log(
    db: DB,
    project: Annotated[Project, Depends(project_permission("audit.read"))],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = None,
) -> Page[AuditOut]:
    stmt = select(AuditEvent).where(AuditEvent.project_id == project.id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).all()
    return Page[AuditOut](
        items=[AuditOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/projects/{project_id}/events", response_model=list[EventOut])
def list_events(
    project: ReadProject,
    db: DB,
    after: int = Query(0, ge=0),
    run_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[EventOut]:
    return [
        EventOut.model_validate(events.to_envelope(e).model_dump())
        for e in events.events_after(db, project.id, after, run_id=run_id, limit=limit)
    ]


@router.get("/projects/{project_id}/events/stream", response_class=StreamingResponse)
async def stream_events(
    project_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
    db: DB,
    after: int = Query(0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-sent events. Resume with `Last-Event-ID` (event `seq`). Clients deduplicate by `event_id`."""
    authorize(db, principal, project_id, "project.read")
    # Release the request's session now. Holding its transaction for the life of the stream would
    # pin locks and block DDL that waits for older transactions (e.g. CREATE INDEX CONCURRENTLY in
    # the checkpointer's one-time setup). Each poll below opens and closes its own session.
    db.close()
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

    def fetch(cursor: int) -> list[dict[str, object]]:
        with SessionLocal() as s:
            return [
                events.to_envelope(e).model_dump(mode="json")
                for e in events.events_after(s, project_id, cursor)
            ]

    async def gen() -> AsyncIterator[str]:
        cursor, idle = start, 0
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(fetch, cursor)
            for env in batch:
                cursor = int(env["seq"])  # type: ignore[call-overload]
                yield f"id: {cursor}\nevent: {env['type']}\ndata: {json.dumps(env)}\n\n"
            if batch:
                idle = 0
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
