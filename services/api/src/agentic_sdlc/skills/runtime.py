"""Skill runtime: strategy selection, validation, bounded retries, budgets, and full provenance."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from agentic_sdlc.core.db import SessionLocal, utcnow
from agentic_sdlc.core.errors import BudgetExceeded, DomainError, PolicyViolation
from agentic_sdlc.core.logging import log
from agentic_sdlc.core.tracing import span
from agentic_sdlc.models.registry import ModelRegistry, get_registry, record_observation
from agentic_sdlc.models.router import RoutingDecision, RoutingRequest, route
from agentic_sdlc.models.tokens import estimate_tokens
from agentic_sdlc.models.types import ModelEntry, ModelError
from agentic_sdlc.orm import AgentRun, TokenUsage
from agentic_sdlc.skills.base import (
    ExecutionContext,
    ModelCall,
    SkillContext,
    SkillError,
    SkillOutputInvalid,
    SkillUnavailable,
)
from agentic_sdlc.skills.manifest import SkillManifest
from agentic_sdlc.skills.registry import SkillRegistry, get_skill_registry
from agentic_sdlc.skills.tools import ToolGateway

logger = logging.getLogger("agentic_sdlc.skills")


@dataclass
class SkillResult:
    output: dict[str, Any]
    strategy: str
    model_ref: str | None
    is_mock: bool
    agent_run_id: Any
    attempts: int
    warnings: list[str] = field(default_factory=list)
    routing: dict[str, Any] = field(default_factory=dict)


def _errors(schema: dict[str, Any], value: Any) -> list[str]:
    v = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in v.iter_errors(value)
    ][:20]


class SkillRuntime:
    def __init__(self, registry: SkillRegistry | None = None, models: ModelRegistry | None = None) -> None:
        self.registry = registry or get_skill_registry()
        self._models = models

    def models(self, ctx: ExecutionContext) -> ModelRegistry:
        return ctx.model_registry or self._models or get_registry()

    def _choose(
        self, manifest: SkillManifest, inputs: dict[str, Any], ctx: ExecutionContext
    ) -> tuple[str, RoutingDecision | None, str | None]:
        """Return (strategy, routing decision, fallback reason)."""
        if ctx.force_strategy:
            if ctx.force_strategy not in manifest.strategies:
                raise SkillUnavailable(f"{manifest.ref} has no strategy '{ctx.force_strategy}'")
            if ctx.force_strategy != "llm":
                return ctx.force_strategy, None, "strategy forced by caller"
        decision: RoutingDecision | None = None
        fallback: str | None = None
        if "llm" in manifest.strategies and manifest.model is not None:
            decision = route(
                self.models(ctx),
                RoutingRequest(
                    task=manifest.model.task,
                    capabilities=manifest.model.capabilities,
                    context_tokens=min(estimate_tokens(str(inputs)), manifest.budgets.max_input_tokens),
                    expected_output_tokens=manifest.model.expected_output_tokens,
                ),
                ctx.observations,
            )
            if decision.model_id is not None:
                return "llm", decision, None
            fallback = decision.fallback_reason
            if ctx.force_strategy == "llm":
                raise SkillUnavailable(f"{manifest.ref}: llm strategy forced but {fallback}")
        if "heuristic" in manifest.strategies:
            return "heuristic", decision, fallback or "no llm strategy declared"
        if "scripted" in manifest.strategies and ctx.allow_scripted:
            return "scripted", decision, fallback or "scripted strategy permitted for sample repository"
        raise SkillUnavailable(f"{manifest.ref}: no runnable strategy ({fallback or 'none declared'})")

    def execute(self, skill_id: str, inputs: dict[str, Any], ctx: ExecutionContext) -> SkillResult:
        manifest = self.registry.get(skill_id)
        impl = self.registry.impls[skill_id]
        agent_def = self.registry.agents.get(ctx.agent)
        if (
            agent_def is None
            or skill_id not in agent_def.get("skills", [])
            or ctx.agent not in manifest.agents
        ):
            raise PolicyViolation(f"agent '{ctx.agent}' may not run skill '{skill_id}'")

        input_errors = _errors(manifest.input_schema, inputs)
        if input_errors:
            raise SkillError(f"{manifest.ref}: invalid input: {input_errors}")

        strategy, decision, fallback = self._choose(manifest, inputs, ctx)
        models = self.models(ctx)
        entry = (
            models.get(decision.model_id) if decision and decision.model_id and strategy == "llm" else None
        )
        # Telemetry (agent run, tool invocations, token usage) is written in its own session and committed
        # immediately: tokens spent and failures are facts even if the caller's transaction rolls back.
        tel = SessionLocal()
        try:
            run = AgentRun(
                project_id=ctx.project_id,
                run_id=ctx.run_id,
                stage=ctx.stage,
                agent=ctx.agent,
                skill_id=manifest.id,
                skill_version=manifest.version,
                strategy=strategy,
                model_ref=entry.ref if entry else None,
                is_mock=bool(entry and entry.is_mock),
                status="running",
                input_summary={"keys": sorted(inputs)},
                routing={"decision": decision.summary() if decision else None, "fallback_reason": fallback},
            )
            tel.add(run)
            tel.commit()
            return self._attempts(manifest, impl, inputs, ctx, tel, run, entry, models, agent_def)
        finally:
            tel.close()

    def _attempts(
        self,
        manifest: SkillManifest,
        impl: Any,
        inputs: dict[str, Any],
        ctx: ExecutionContext,
        tel: Session,
        run: AgentRun,
        entry: ModelEntry | None,
        models: ModelRegistry,
        agent_def: dict[str, Any],
    ) -> SkillResult:
        allowed = set(manifest.tools) & set(agent_def.get("tools", []))
        started = time.monotonic()
        attempts = 0
        last_error: Exception | None = None
        with span(
            "skill.execute", skill=manifest.ref, agent=ctx.agent, strategy=run.strategy, run_id=ctx.run_id
        ):
            while attempts < manifest.retry.max_attempts:
                attempts += 1
                purpose = "execution" if attempts == 1 else "retry"

                def on_usage(
                    tin: int,
                    tout: int,
                    estimated: bool,
                    cost: float,
                    latency: int,
                    ok: bool,
                    _p: str = purpose,
                ) -> None:
                    assert entry is not None
                    tel.add(
                        TokenUsage(
                            project_id=ctx.project_id,
                            run_id=ctx.run_id,
                            agent_run_id=run.id,
                            backlog_item_id=ctx.backlog_item_id,
                            model_id=entry.model_id,
                            input_tokens=tin,
                            output_tokens=tout,
                            is_estimated=estimated,
                            cost_usd=cost,
                            purpose=_p,
                        )
                    )
                    record_observation(tel, entry.model_id, success=ok, latency_ms=latency)
                    tel.commit()
                    if ctx.budget:
                        ctx.budget.record_model_call(tin + tout, cost)

                model_call = (
                    ModelCall(
                        entry=entry,
                        registry=models,
                        on_usage=on_usage,
                        budget=ctx.budget,
                        max_output_tokens=manifest.budgets.max_output_tokens,
                        timeout_s=float(manifest.timeout_s),
                    )
                    if entry
                    else None
                )
                sctx = SkillContext(
                    execution=ctx,
                    manifest=manifest,
                    strategy=run.strategy,
                    tools=None,  # type: ignore[arg-type]
                    model=model_call,
                    deadline=started + manifest.timeout_s,
                )
                sctx.tools = ToolGateway(tel, run.id, allowed, sctx)
                try:
                    for name in manifest.preconditions:
                        err = impl.preconditions()[name](inputs, sctx)
                        if err:
                            raise SkillError(f"precondition '{name}' failed: {err}")
                    output = impl.run(run.strategy, inputs, sctx)
                    sctx.check_deadline()
                    out_errors = _errors(manifest.output_schema, output)
                    if out_errors:
                        raise SkillOutputInvalid("output failed schema validation", errors=out_errors)
                    violations = [
                        v
                        for name in manifest.postconditions
                        for v in impl.postconditions()[name](inputs, output, sctx)
                    ]
                    if violations:
                        raise SkillOutputInvalid("postconditions violated", errors=violations)
                except (SkillOutputInvalid, ModelError) as exc:
                    tel.commit()
                    last_error = exc
                    retryable = isinstance(exc, SkillOutputInvalid) or exc.retryable
                    log(
                        logger,
                        logging.WARNING,
                        "skill attempt failed",
                        skill=manifest.ref,
                        attempt=attempts,
                        error=str(exc),
                        details=getattr(exc, "errors", None),
                    )
                    if not retryable or manifest.failure.on_validation_error == "fail":
                        break
                    if manifest.retry.backoff_s and attempts < manifest.retry.max_attempts:
                        time.sleep(min(manifest.retry.backoff_s * attempts, 5))
                    continue
                except (SkillError, DomainError) as exc:
                    tel.commit()
                    last_error = exc
                    break
                except Exception as exc:  # unexpected implementation error: record, then surface
                    tel.rollback()
                    last_error = exc
                    log(logger, logging.ERROR, "skill crashed", skill=manifest.ref, error=repr(exc))
                    break
                run.status = "succeeded"
                run.attempts = attempts
                run.duration_ms = int((time.monotonic() - started) * 1000)
                run.finished_at = utcnow()
                run.output_summary = {"keys": sorted(output), "warnings": sctx.warnings[:20]}
                tel.commit()
                return SkillResult(
                    output=output,
                    strategy=run.strategy,
                    model_ref=run.model_ref,
                    is_mock=run.is_mock,
                    agent_run_id=run.id,
                    attempts=attempts,
                    warnings=sctx.warnings,
                    routing=run.routing,
                )

        run.status = "failed"
        run.attempts = attempts
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.finished_at = utcnow()
        detail = getattr(last_error, "errors", None)
        run.error = (f"{type(last_error).__name__}: {last_error}" + (f" {detail}" if detail else ""))[:4000]
        tel.commit()
        if isinstance(last_error, BudgetExceeded | PolicyViolation):
            raise last_error
        raise SkillError(f"{manifest.ref} failed after {attempts} attempt(s): {last_error}") from last_error
