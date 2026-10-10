"""Model routing is tested independently of the UI (Phase 3 exit criterion)."""

from __future__ import annotations

from typing import Any

import pytest

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.models.providers import MockProvider
from agentic_sdlc.models.registry import ModelRegistry
from agentic_sdlc.models.router import RoutingRequest, route
from agentic_sdlc.models.types import ModelEntry


class FakeProvider(MockProvider):
    def __init__(self, served: set[str]) -> None:
        super().__init__()
        self.served = served

    def available(self, entry: ModelEntry) -> bool:
        return entry.served_model_name in self.served


def entry(model_id: str, **kw: Any) -> ModelEntry:
    base: dict[str, Any] = dict(
        model_id=model_id,
        provider="openai_compatible",
        served_model_name=model_id,
        revision="r1",
        family="f",
        license="Apache-2.0",
        tokenizer="t",
        context_window=32768,
        max_output_tokens=4096,
        tasks=["extraction", "code_generation"],
        capabilities=["structured_output", "code"],
        privacy="self_hosted",
    )
    base.update(kw)
    return ModelEntry(**base)


def registry(*entries: ModelEntry, served: set[str] | None = None) -> ModelRegistry:
    names = served if served is not None else {e.served_model_name for e in entries}
    return ModelRegistry(list(entries), {"openai_compatible": FakeProvider(names), "mock": MockProvider()})


def test_filters_by_task_capability_and_context() -> None:
    reg = registry(
        entry("small", context_window=4096),
        entry("nocode", capabilities=["structured_output"]),
        entry("other-task", tasks=["planning"]),
        entry("good"),
    )
    d = route(reg, RoutingRequest(task="code_generation", capabilities=["code"], context_tokens=8000))
    assert d.model_id == "good"
    reasons = {c.model_id: c.reasons for c in d.candidates}
    assert "exceeds" in reasons["small"][0]
    assert "missing capabilities" in reasons["nocode"][0]
    assert "does not support task" in reasons["other-task"][0]


def test_unavailable_models_are_not_selected() -> None:
    reg = registry(entry("a"), entry("b"), served={"b"})
    assert route(reg, RoutingRequest(task="extraction")).model_id == "b"
    none = route(registry(entry("a"), served=set()), RoutingRequest(task="extraction"))
    assert none.model_id is None and none.fallback_reason == "no eligible model"
    assert "not served" in none.candidates[0].reasons[0]


def test_external_models_excluded_by_privacy_policy() -> None:
    reg = registry(entry("ext", privacy="external"), entry("local"))
    assert route(reg, RoutingRequest(task="extraction")).model_id == "local"
    assert (
        route(
            registry(entry("ext", privacy="external")), RoutingRequest(task="extraction", privacy="any")
        ).model_id
        == "ext"
    )


def test_mock_requires_explicit_enablement() -> None:
    mock = entry("mock", provider="mock", privacy="in_process", is_mock=True)
    reg = registry(mock)
    assert route(reg, RoutingRequest(task="extraction")).model_id is None
    get_settings().allow_mock_models = True
    try:
        d = route(reg, RoutingRequest(task="extraction"))
        assert d.model_id == "mock" and d.is_mock
    finally:
        get_settings().allow_mock_models = False


def test_observed_reliability_beats_cheapness_and_priority() -> None:
    cheap = entry("cheap", priority=50)
    reliable = entry("reliable", priority=10, pricing={"input_per_1k_usd": 0.001, "output_per_1k_usd": 0.002})
    obs = {
        "cheap": {"calls": 50, "successes": 10, "mean_latency_ms": 500},
        "reliable": {"calls": 50, "successes": 49, "mean_latency_ms": 900},
    }
    d = route(registry(cheap, reliable), RoutingRequest(task="extraction", expected_output_tokens=1000), obs)
    assert d.model_id == "reliable"
    # Without evidence, configured priority decides.
    assert route(registry(cheap, reliable), RoutingRequest(task="extraction")).model_id == "cheap"


def test_budget_and_license_filters_and_exclusions() -> None:
    pricey = entry("pricey", pricing={"input_per_1k_usd": 1.0, "output_per_1k_usd": 1.0})
    unverified = entry("unverified")
    verified = entry("verified", license_verified=True)
    reg = registry(pricey, unverified, verified)
    d = route(
        reg,
        RoutingRequest(
            task="extraction", context_tokens=10_000, max_cost_usd=0.5, require_verified_license=True
        ),
    )
    assert d.model_id == "verified"
    assert (
        route(reg, RoutingRequest(task="extraction", excluded=["verified", "pricey"])).model_id
        == "unverified"
    )


def test_deterministic_tie_break() -> None:
    reg = registry(entry("zeta"), entry("alpha"))
    assert {route(reg, RoutingRequest(task="extraction")).model_id for _ in range(5)} == {"alpha"}


def test_default_registry_has_licensing_metadata() -> None:
    reg = ModelRegistry.from_file(get_settings().model_registry_path)
    for e in reg.entries.values():
        assert e.license and e.revision and e.context_window > 0
    qwen = reg.get("qwen2.5-7b-instruct")
    assert qwen.license_verified is False  # honest until someone verifies the model card


@pytest.mark.parametrize("ctx", [0, 32768 - 1024])
def test_context_boundary(ctx: int) -> None:
    reg = registry(entry("m"))
    assert (
        route(
            reg, RoutingRequest(task="extraction", context_tokens=ctx, expected_output_tokens=1024)
        ).model_id
        == "m"
    )
    assert (
        route(
            reg,
            RoutingRequest(task="extraction", context_tokens=ctx + 1 + 32768, expected_output_tokens=1024),
        ).model_id
        is None
    )
