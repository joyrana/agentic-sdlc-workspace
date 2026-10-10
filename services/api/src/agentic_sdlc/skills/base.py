"""Base classes and context handed to skill implementations."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy.orm import Session

from agentic_sdlc.core.errors import BudgetExceeded
from agentic_sdlc.models.types import Message, ModelError, ModelRequest

if TYPE_CHECKING:
    from agentic_sdlc.models.registry import ModelRegistry
    from agentic_sdlc.models.types import ModelEntry
    from agentic_sdlc.skills.manifest import SkillManifest
    from agentic_sdlc.skills.tools import ToolGateway


class SkillError(Exception):
    """Non-retryable skill failure."""


class SkillOutputInvalid(Exception):
    """Output failed schema or semantic validation; retryable under the manifest's policy."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class SkillUnavailable(SkillError):
    """No strategy can run (e.g. no model and no heuristic)."""


class SkillTimeout(SkillError):
    pass


@dataclass
class Limits:
    max_model_calls: int
    max_tokens: int
    max_cost_usd: float
    max_wall_clock_s: int


@dataclass
class BudgetTracker:
    """Enforces per-run budgets. Counters are persisted by the caller (workflow run row)."""

    limits: Limits
    counters: dict[str, Any] = field(default_factory=dict)
    started_monotonic: float = field(default_factory=time.monotonic)
    on_change: Callable[[dict[str, Any]], None] | None = None

    def _get(self, key: str) -> float:
        return float(self.counters.get(key, 0))

    def check_model_call(self, estimated_tokens: int, estimated_cost: float) -> None:
        if self._get("model_calls") + 1 > self.limits.max_model_calls:
            raise BudgetExceeded(
                "model call budget exhausted", details={"limit": self.limits.max_model_calls}
            )
        if self._get("tokens") + estimated_tokens > self.limits.max_tokens:
            raise BudgetExceeded("token budget exhausted", details={"limit": self.limits.max_tokens})
        if self._get("cost_usd") + estimated_cost > self.limits.max_cost_usd:
            raise BudgetExceeded("cost budget exhausted", details={"limit": self.limits.max_cost_usd})
        self.check_time()

    def check_time(self) -> None:
        elapsed = float(self.counters.get("elapsed_s", 0)) + (time.monotonic() - self.started_monotonic)
        if elapsed > self.limits.max_wall_clock_s:
            raise BudgetExceeded(
                "wall-clock budget exhausted", details={"limit_s": self.limits.max_wall_clock_s}
            )

    def record_model_call(self, tokens: int, cost: float) -> None:
        self.counters["model_calls"] = int(self._get("model_calls")) + 1
        self.counters["tokens"] = int(self._get("tokens")) + tokens
        self.counters["cost_usd"] = round(self._get("cost_usd") + cost, 6)
        if self.on_change:
            self.on_change(dict(self.counters))

    def increment(self, key: str, by: int = 1) -> int:
        self.counters[key] = int(self._get(key)) + by
        if self.on_change:
            self.on_change(dict(self.counters))
        return int(self.counters[key])


@dataclass
class ExecutionContext:
    """Everything a skill execution needs from its caller (workflow node, intake job, test)."""

    db: Session
    project_id: uuid.UUID
    agent: str
    run_id: uuid.UUID | None = None
    stage: str | None = None
    backlog_item_id: uuid.UUID | None = None
    budget: BudgetTracker | None = None
    model_registry: ModelRegistry | None = None
    force_strategy: str | None = None
    allow_scripted: bool = False
    observations: dict[str, dict[str, Any]] | None = None


@dataclass
class ModelCall:
    """Bound model access for an `llm` strategy execution. Records usage; enforces budgets."""

    entry: ModelEntry
    registry: ModelRegistry
    on_usage: Callable[[int, int, bool, float, int, bool], None]
    budget: BudgetTracker | None
    max_output_tokens: int
    timeout_s: float

    def structured(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        from agentic_sdlc.models.tokens import estimate_messages

        est_in = estimate_messages([system, user])
        if self.budget:
            self.budget.check_model_call(
                est_in + self.max_output_tokens, self.entry.cost(est_in, self.max_output_tokens)
            )
        provider = self.registry.provider_for(self.entry)
        if provider is None:
            raise ModelError("provider unavailable", retryable=False)
        request = ModelRequest(
            messages=[Message(role="system", content=system), Message(role="user", content=user)],
            json_schema=schema,
            max_output_tokens=self.max_output_tokens,
            timeout_s=self.timeout_s,
        )
        try:
            resp = provider.generate(self.entry, request)
        except ModelError:
            self.on_usage(est_in, 0, True, 0.0, 0, False)
            raise
        cost = self.entry.cost(resp.input_tokens, resp.output_tokens)
        self.on_usage(
            resp.input_tokens, resp.output_tokens, resp.usage_estimated, cost, resp.latency_ms, True
        )
        try:
            parsed = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise SkillOutputInvalid("model returned invalid JSON", errors=[str(exc)]) from exc
        if not isinstance(parsed, dict):
            raise SkillOutputInvalid("model output must be a JSON object")
        return parsed


@dataclass
class SkillContext:
    """What an implementation can touch: tools (permissioned), an optional model, warnings."""

    execution: ExecutionContext
    manifest: SkillManifest
    strategy: str
    tools: ToolGateway
    model: ModelCall | None
    deadline: float
    warnings: list[str] = field(default_factory=list)

    @property
    def db(self) -> Session:
        return self.execution.db

    def check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise SkillTimeout(f"skill exceeded {self.manifest.timeout_s}s timeout")


class SkillImplementation:
    """Subclass per skill. `strategies` must cover the manifest's strategies."""

    strategies: ClassVar[set[str]] = set()

    def run(self, strategy: str, inputs: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
        raise NotImplementedError

    def preconditions(self) -> dict[str, Callable[[dict[str, Any], SkillContext], str | None]]:
        """name -> check(inputs, ctx) returning an error message or None."""
        return {}

    def postconditions(
        self,
    ) -> dict[str, Callable[[dict[str, Any], dict[str, Any], SkillContext], list[str]]]:
        """name -> check(inputs, output, ctx) returning a list of violations."""
        return {}
