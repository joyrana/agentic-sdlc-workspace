# Testing and Evaluation Strategy

## Test layers (all run with `make test`)

| Layer | Location | What it proves |
|---|---|---|
| Unit | `services/api/tests/unit` | parsers, schemas, policies, routing, estimation, cycle detection, redaction, patch validation |
| Integration | `services/api/tests/integration` | migrations, persistence, API authorization, skill loading, checkpoints, job queue |
| Agent contract | `services/api/tests/contract` | structured output validation, malformed model output, timeouts, retry exhaustion, tool allow-lists |
| Workflow | `services/api/tests/workflow` | happy path, rejection, changes requested, stale approval, restart/resume, duplicate jobs, budget exhaustion, repair-loop termination |
| Security | `services/api/tests/security` | cross-project access, self-approval, malicious DOCX, injection, sandbox isolation, deploy restrictions |
| End-to-end (API) | `services/api/tests/e2e` | full document → staging release journey with real Postgres, real sandbox, scripted coding agent |
| Frontend | `apps/web` | type check, lint, unit tests (Vitest), production build |

Tests run against a real PostgreSQL database (`sdlc_test`). There is no SQLite fallback,
because behavior (JSONB, `SKIP LOCKED`, triggers) differs.

## Evaluations

`evals/` holds versioned datasets with ground truth and a runner (`python -m evals.run`) that
records dataset version, skill version, strategy, model reference, raw outputs, metrics and
cost into `evaluation_runs`.

Initial suites (baseline = heuristic strategy):

| Suite | Metric(s) |
|---|---|
| `requirements` | precision/recall of requirement keys and statements vs ground truth; locator validity rate |
| `ambiguity` | recall of flagged ambiguous terms / TBDs |
| `planning` | dependency graph validity (acyclic, all criteria covered by ≥1 story) |
| `end-to-end` | stages completed, repair attempts used, gates enforced |

Rule: a change to a skill, prompt, router policy or graph must not reduce a suite's primary
metric below the committed baseline in `evals/baselines/` (CI enforces).

LLM-as-judge is **not** used yet; it will only be introduced with human-calibrated rubrics.

## Model-backed tests

Marked `@pytest.mark.model`; skipped unless `MODEL_ENDPOINT_*` is configured. They run the
same eval suites through the `llm` strategy.
