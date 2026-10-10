from __future__ import annotations

from fastapi import FastAPI

from agentic_sdlc.api.routers import projects, system


def register_routers(app: FastAPI) -> None:
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
