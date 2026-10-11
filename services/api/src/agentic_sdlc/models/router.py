"""Deterministic model routing policy.

Hard filters (task, capabilities, context, privacy, availability, mock policy, license verification
when required) remove candidates and record why. Remaining candidates are scored on observed
success rate, configured priority, latency and cost. Ties break on model_id for determinism.
Neither "cheapest" nor "largest" is assumed best: the score weights are explicit and tested.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_sdlc.models.registry import ModelRegistry
from agentic_sdlc.models.types import ModelEntry


class RoutingRequest(BaseModel):
    task: str
    capabilities: list[str] = Field(default_factory=list)
    context_tokens: int = 0
    expected_output_tokens: int = 1024
    privacy: Literal["any", "self_hosted_only"] = "self_hosted_only"
    max_cost_usd: float | None = None
    require_verified_license: bool = False
    excluded: list[str] = Field(default_factory=list)  # e.g. models that already failed this task


class Candidate(BaseModel):
    model_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    score: float = 0.0


class RoutingDecision(BaseModel):
    model_id: str | None
    model_ref: str | None
    is_mock: bool = False
    candidates: list[Candidate]
    fallback_reason: str | None = None

    def summary(self) -> dict[str, Any]:
        return self.model_dump()


def _observed(observations: dict[str, dict[str, Any]], model_id: str) -> tuple[float, float]:
    obs = observations.get(model_id, {})
    calls = int(obs.get("calls", 0))
    # Prior of 0.8 success with weight 5 calls avoids over-trusting tiny samples.
    rate = (float(obs.get("successes", 0)) + 0.8 * 5) / (calls + 5)
    latency = float(obs.get("mean_latency_ms", 0.0))
    return rate, latency


def route(
    registry: ModelRegistry, req: RoutingRequest, observations: dict[str, dict[str, Any]] | None = None
) -> RoutingDecision:
    observations = observations or {}
    candidates: list[Candidate] = []
    eligible: list[tuple[float, ModelEntry]] = []
    for entry in sorted(registry.entries.values(), key=lambda e: e.model_id):
        reasons: list[str] = []
        if entry.model_id in req.excluded:
            reasons.append("excluded for this task")
        if req.task not in entry.tasks:
            reasons.append(f"does not support task '{req.task}'")
        missing = set(req.capabilities) - set(entry.capabilities)
        if missing:
            reasons.append(f"missing capabilities {sorted(missing)}")
        if req.context_tokens + req.expected_output_tokens > entry.context_window:
            reasons.append(
                f"context {req.context_tokens}+{req.expected_output_tokens} exceeds {entry.context_window}"
            )
        if req.privacy == "self_hosted_only" and entry.privacy == "external":
            reasons.append("external provider not allowed by privacy policy")
        if req.require_verified_license and not entry.license_verified:
            reasons.append("license not verified")
        cost = entry.cost(req.context_tokens, req.expected_output_tokens)
        if req.max_cost_usd is not None and cost > req.max_cost_usd:
            reasons.append(f"estimated cost {cost} exceeds budget {req.max_cost_usd}")
        if not reasons and not registry.is_available(entry):
            reasons.append(
                "mock models disabled" if entry.is_mock else "provider not configured or model not served"
            )
        if reasons:
            candidates.append(Candidate(model_id=entry.model_id, eligible=False, reasons=reasons))
            continue
        rate, latency = _observed(observations, entry.model_id)
        score = round(100 * rate + entry.priority * 0.2 - latency / 1000.0 - cost * 10, 4)
        candidates.append(Candidate(model_id=entry.model_id, eligible=True, score=score))
        eligible.append((score, entry))

    if not eligible:
        return RoutingDecision(
            model_id=None, model_ref=None, candidates=candidates, fallback_reason="no eligible model"
        )
    best = sorted(eligible, key=lambda x: (-x[0], x[1].model_id))[0][1]
    return RoutingDecision(
        model_id=best.model_id, model_ref=best.ref, is_mock=best.is_mock, candidates=candidates
    )
