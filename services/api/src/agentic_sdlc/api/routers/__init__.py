from __future__ import annotations

from fastapi import FastAPI

from agentic_sdlc.api.routers import backlog, projects, requirements, runs, system


def register_routers(app: FastAPI) -> None:
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(requirements.router, prefix="/api/v1")
    app.include_router(backlog.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(backlog.admin_router, prefix="/api/v1")
