# Testing and Evaluation Strategy

## Test layers

| Layer | Location | What it proves |
|---|---|---|
| Unit | `services/api/tests/unit` | extraction rules, planning, graph algorithms, routing, patch policy |
| Integration | `services/api/tests/integration` | migrations, persistence, API authorization, rate limiting, audit immutability |
| Agent contract | `services/api/tests/contract` | schema validation, malformed model output, timeouts, retry exhaustion, tool allow-lists |
| Workflow | `services/api/tests/workflow` | gated flow, rejection, changes requested, stale approvals, restart/resume in a separate process, budgets, repair-loop termination |
| Security | `services/api/tests/security` | malicious uploads, sandbox isolation, git hardening |
| End-to-end (API) | `services/api/tests/e2e` | document → staging release, blocked release, smoke failure → rollback |
| Evaluation harness | `services/api/tests/evals` | suites meet baselines; a regressed component is caught |
| Frontend unit | `apps/web/src/**/*.test.tsx` | approval UI states, honest strategy labels, delivery rail |
| Browser E2E | `apps/web/e2e` | the 15-step slice through the UI, one signed-in person per role |
| Deployment smoke | `scripts/smoke_slice.py` | the 15-step slice over HTTP against the Docker Compose stack |

Tests run against a real PostgreSQL database; there is no SQLite fallback, because JSONB,
`SKIP LOCKED` and triggers behave differently. Sandbox tests need root and are skipped
otherwise; CI runs pytest as root and fails if any sandbox test was skipped.

## Offline evaluations

`evals/<suite>/cases.json` holds versioned, hand-labelled datasets.
`python -m agentic_sdlc.evals.run --all` scores each suite, compares the metrics with
`evals/baselines.json` and exits non-zero on a regression. `--report PATH` writes JSON;
`--record` stores each suite as an `EvaluationRun` (dataset version, git SHA, skill versions,
strategy, metrics, per-case results), listed at `GET /api/v1/evaluations` and in
**Capabilities → Evaluations**.

| Suite | Component | Metrics |
|---|---|---|
| `requirements` | BRD and transcript extraction | statement recall and precision, kind and key accuracy, locator validity, injection recall and false-positive rate, contradiction recall and false-positive rate, ambiguity-term recall |
| `planning` | decomposition and dependency analysis | requirement coverage, cycle detection, milestone ordering |
| `patch_policy` | developer patch review | block recall, allow rate, risky-code warnings |
| `release_policy` | release eligibility | exact blocker match, unsafe releases (must be 0) |
| `redaction` | log and tool-argument redaction | secrets removed, benign text unchanged |

The runner also checks each skill manifest's `evaluation.success_criteria` and reports every
criterion as met, not met, or **not measured offline** (for example the coding agent's acceptance
pass rate, which only the end-to-end tests exercise).

Baselines: safety metrics are pinned at their only acceptable value. Quality metrics record the
measured rule-based baseline; they are raised in a reviewed change when a component improves and
never lowered to make a run pass.

## Model-backed evaluation

Not available in this environment (no model endpoint). The datasets are model-agnostic; running
them through the `llm` strategy and recording model, tokens and cost per case is the next step
once an endpoint is configured. LLM-as-judge is not used; it would need human-calibrated rubrics
first.
