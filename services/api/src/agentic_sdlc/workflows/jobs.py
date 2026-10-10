"""Postgres-backed job queue (ADR-0002).

- `enqueue` is idempotent on `dedup_key` (INSERT ... ON CONFLICT DO NOTHING).
- `claim` uses FOR UPDATE SKIP LOCKED so many workers can poll safely, and reclaims jobs whose
  lease expired (a worker died mid-job). Handlers must therefore be idempotent.
- Failures retry with exponential backoff up to `max_attempts`, then the job is `failed`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agentic_sdlc.core.db import utcnow
from agentic_sdlc.orm import WorkflowJob

LEASE = timedelta(minutes=15)


def enqueue(
    db: Session,
    *,
    kind: str,
    dedup_key: str,
    payload: dict[str, Any] | None = None,
    run_id: uuid.UUID | None = None,
    max_attempts: int = 3,
) -> WorkflowJob:
    stmt = (
        insert(WorkflowJob)
        .values(
            id=uuid.uuid4(),
            kind=kind,
            dedup_key=dedup_key,
            payload=payload or {},
            run_id=run_id,
            status="queued",
            attempts=0,
            max_attempts=max_attempts,
            available_at=utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"])
    )
    db.execute(stmt)
    job = db.scalar(select(WorkflowJob).where(WorkflowJob.dedup_key == dedup_key))
    assert job is not None
    return job


def claim(db: Session) -> WorkflowJob | None:
    now = utcnow()
    job = db.scalar(
        select(WorkflowJob)
        .where(
            or_(
                (WorkflowJob.status == "queued") & (WorkflowJob.available_at <= now),
                (WorkflowJob.status == "running") & (WorkflowJob.lease_expires_at < now),
            )
        )
        .order_by(WorkflowJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.lease_expires_at = now + LEASE
    return job


def complete(db: Session, job_id: uuid.UUID) -> None:
    job = db.get(WorkflowJob, job_id)
    if job is not None:
        job.status = "done"
        job.lease_expires_at = None


def fail(db: Session, job_id: uuid.UUID, error: str) -> WorkflowJob | None:
    job = db.get(WorkflowJob, job_id)
    if job is None:
        return None
    job.last_error = error[:4000]
    job.lease_expires_at = None
    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "queued"
        job.available_at = utcnow() + timedelta(seconds=min(300, 5 * 2 ** (job.attempts - 1)))
    return job
