"""Shared FastAPI dependencies."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock
from typing import Annotated

from fastapi import Depends, Header, Path
from sqlalchemy.orm import Session

from agentic_sdlc.auth.service import Principal, authenticate, authorize
from agentic_sdlc.core.db import get_db
from agentic_sdlc.core.errors import RateLimited
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import Project

DB = Annotated[Session, Depends(get_db)]


class _SlidingWindowLimiter:
    """Per-process sliding-window limiter keyed by token id. Documented limitation: not shared across
    processes; put a gateway limiter in front for multi-instance deployments."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                raise RateLimited(
                    "rate limit exceeded", details={"retry_after_s": int(60 - (now - q[0])) + 1}
                )
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = _SlidingWindowLimiter()


def current_principal(db: DB, authorization: Annotated[str | None, Header()] = None) -> Principal:
    principal = authenticate(db, authorization)
    limiter.check(str(principal.token_id or principal.user_id), get_settings().rate_limit_per_minute)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def project_permission(permission: str) -> Callable[..., Project]:
    def dep(
        db: DB,
        principal: CurrentPrincipal,
        project_id: Annotated[uuid.UUID, Path()],
    ) -> Project:
        return authorize(db, principal, project_id, permission)

    return dep
