"""ASGI entry point: `uvicorn agentic_sdlc.api.main:app`."""

from agentic_sdlc.api.app import create_app

app = create_app()
