"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm.exc import StaleDataError

from agentic_sdlc import __version__
from agentic_sdlc.core.errors import DomainError
from agentic_sdlc.core.logging import configure_logging, correlation_id_var, log
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.core.tracing import configure_tracing, span

logger = logging.getLogger("agentic_sdlc.api")


def _error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "correlation_id": correlation_id_var.get(),
            }
        },
        headers={"X-Request-ID": correlation_id_var.get() or ""},
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    configure_tracing("agentic-sdlc-api")

    app = FastAPI(
        title="Agentic SDLC Workspace API",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        cid = (
            incoming
            if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum()
            else str(uuid.uuid4())
        )
        token = correlation_id_var.set(cid)
        start = time.perf_counter()
        try:
            with span("http.request", method=request.method, route=request.url.path):
                response = await call_next(request)
            response.headers["X-Request-ID"] = cid
            log(
                logger,
                logging.INFO,
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return response
        finally:
            correlation_id_var.reset(token)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(StaleDataError)
    async def stale(_: Request, exc: StaleDataError) -> JSONResponse:
        return _error(409, "conflict", "resource was modified concurrently; reload and retry")

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return _error(422, "validation_failed", "request validation failed", {"errors": errors})

    from agentic_sdlc.api.routers import register_routers

    register_routers(app)
    return app
