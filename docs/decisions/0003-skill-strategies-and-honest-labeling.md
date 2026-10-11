# ADR-0003: Skill execution strategies and labeling of non-model output

- Status: Accepted
- Date: 2026-10-10

## Context

The target model family is Qwen served locally (e.g. vLLM). The build environment has no GPU,
so no real inference can be run or tested here. The spec forbids presenting mocked behavior
as real.

## Decision

Each skill implementation may provide several strategies:

| Strategy | Meaning |
|---|---|
| `llm` | Uses a model through the router; output validated against the skill's JSON Schema plus skill-specific checks (e.g. cited source text must exist at the cited locator). |
| `heuristic` | Deterministic, rule-based implementation. A legitimate baseline, used when no suitable model is available, and the reference for evaluations. |
| `scripted` | Replays fixture outputs (used only by the coding agent for registered fixture repositories). |
| `mock` | Deterministic mock model provider; used in contract tests. Never selected by the router in non-test environments unless explicitly enabled. |

The selected strategy, model reference (if any) and `is_mock` flag are recorded on every
agent run, token usage record and derived artifact (`extraction_method`), returned by the API,
and displayed in the UI.

The router chooses `llm` only when an available registry entry satisfies the skill's
capability, context and license requirements; otherwise it falls back to `heuristic` and
records the fallback reason.

## Consequences

The platform is fully usable without a GPU, and its outputs are honest about their origin.
Quality of heuristic extraction is bounded; the evaluation suite measures it as a baseline.
