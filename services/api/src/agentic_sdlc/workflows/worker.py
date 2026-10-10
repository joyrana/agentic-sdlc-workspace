"""Workflow worker: claims jobs and dispatches them to handlers."""

from __future__ import annotations

import logging
import signal
import time
import traceback
import uuid
from collections.abc import Callable
from typing import Any

from agentic_sdlc.core.db import session_scope
from agentic_sdlc.core.logging import configure_logging, correlation_id_var, log
from agentic_sdlc.core.tracing import configure_tracing, span
from agentic_sdlc.workflows import jobs

logger = logging.getLogger("agentic_sdlc.worker")

Handler = Callable[[dict[str, Any], uuid.UUID | None], None]


def handlers() -> dict[str, Handler]:
    from agentic_sdlc.workflows import intake, planning_job

    return {"intake": intake.handle, "plan": planning_job.handle}


class NonRetryable(Exception):
    """Raise from a handler to fail the job immediately."""


def process_one() -> bool:
    """Claim and process a single job. Returns False when the queue is empty."""
    with session_scope() as db:
        job = jobs.claim(db)
        if job is None:
            return False
        job_id, kind, payload, run_id, attempt = job.id, job.kind, dict(job.payload), job.run_id, job.attempts
    token = correlation_id_var.set(str(payload.get("correlation_id") or job_id))
    try:
        handler = handlers().get(kind)
        if handler is None:
            raise NonRetryable(f"no handler for job kind '{kind}'")
        with span("job.process", kind=kind, job_id=str(job_id), attempt=attempt):
            handler(payload, run_id)
        with session_scope() as db:
            jobs.complete(db, job_id)
        log(logger, logging.INFO, "job done", kind=kind, job_id=str(job_id))
    except Exception as exc:
        log(
            logger,
            logging.ERROR,
            "job failed",
            kind=kind,
            job_id=str(job_id),
            error=str(exc),
            trace=traceback.format_exc(limit=5),
        )
        with session_scope() as db:
            job2 = jobs.fail(db, job_id, f"{type(exc).__name__}: {exc}")
            if isinstance(exc, NonRetryable) and job2 is not None:
                job2.status = "failed"
    finally:
        correlation_id_var.reset(token)
    return True


def drain(max_jobs: int = 1000) -> int:
    """Process jobs until the queue is empty (used by tests and `worker --once`)."""
    n = 0
    while n < max_jobs and process_one():
        n += 1
    return n


def run_worker(*, once: bool = False, poll_s: float = 1.0) -> None:
    configure_logging()
    configure_tracing("agentic-sdlc-worker")
    from agentic_sdlc.core.db import session_scope as scope
    from agentic_sdlc.models.registry import get_registry
    from agentic_sdlc.skills.registry import get_skill_registry

    with scope() as db:
        get_skill_registry().sync_to_db(db)
        get_registry().sync_to_db(db)
    if once:
        drain()
        return
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log(logger, logging.INFO, "worker started")
    while not stop:
        if not process_one():
            time.sleep(poll_s)
