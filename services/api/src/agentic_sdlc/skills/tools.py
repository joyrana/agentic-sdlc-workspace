"""Permissioned tool gateway. Every call is policy-checked and recorded as a ToolInvocation."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from agentic_sdlc.core.errors import PolicyViolation
from agentic_sdlc.core.logging import redact_obj
from agentic_sdlc.orm import ToolInvocation

ToolFn = Callable[..., Any]
_TOOLS: dict[str, ToolFn] = {}

KNOWN_TOOLS = {
    "documents.read",
    "requirements.read",
    "backlog.read",
    "repo.read",
    "repo.workspace",
    "sandbox.exec",
    "fixtures.read",
    "evidence.read",
    "release.read",
}


def tool(name: str) -> Callable[[ToolFn], ToolFn]:
    if name not in KNOWN_TOOLS:
        raise ValueError(f"unknown tool {name}")

    def deco(fn: ToolFn) -> ToolFn:
        _TOOLS[name] = fn
        return fn

    return deco


def _summarize(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return {"type": "object", "keys": sorted(result)[:20]}
    if isinstance(result, list):
        return {"type": "list", "length": len(result)}
    return {"type": type(result).__name__}


class ToolGateway:
    def __init__(self, db: Session, agent_run_id: uuid.UUID, allowed: set[str], context: Any) -> None:
        self.db = db
        self.agent_run_id = agent_run_id
        self.allowed = allowed
        self.context = context  # SkillContext; passed to tool functions

    def call(self, name: str, /, **kwargs: Any) -> Any:
        args = redact_obj({k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in kwargs.items()})
        if name not in self.allowed or name not in _TOOLS:
            self.db.add(
                ToolInvocation(
                    agent_run_id=self.agent_run_id,
                    tool=name,
                    arguments=args,
                    policy_decision="denied",
                    status="denied",
                )
            )
            self.db.flush()
            raise PolicyViolation(f"tool '{name}' is not permitted for this agent/skill")
        start = time.perf_counter()
        status, summary = "ok", {}
        try:
            result = _TOOLS[name](self.context, **kwargs)
            summary = _summarize(result)
            return result
        except Exception as exc:
            status, summary = "error", {"error": type(exc).__name__}
            raise
        finally:
            self.db.add(
                ToolInvocation(
                    agent_run_id=self.agent_run_id,
                    tool=name,
                    arguments=args,
                    policy_decision="allowed",
                    status=status,
                    duration_ms=int((time.perf_counter() - start) * 1000),
                    result_summary=summary,
                )
            )
