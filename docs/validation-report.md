# Validation Report

What was verified for phases 1–8, how, and what was found and fixed along the way. Results
are from the final tree of the Phase 8 branch (2026-10-11); CI re-runs all of it on every PR.

## Environment

Linux x86_64, 2 vCPU, 7 GB RAM, no GPU. Python 3.13 locally and 3.12 in CI, Node 22,
PostgreSQL 16. Docker Hub was blocked in the build environment, so container images and
the Compose stack are verified in GitHub Actions only.

## Results

| Check | How | Result |
|---|---|---|
| Backend tests | `pytest` as root against PostgreSQL (unit, integration, contract, workflow, security, e2e, eval harness) | 129 passed, 0 skipped |
| Frontend unit tests | Vitest | 8 passed |
| Lint and types | ruff, ruff format, mypy, ESLint, Prettier, tsc | clean |
| Migrations | `alembic upgrade head` then `alembic check` on an empty database | no drift |
| API contract | OpenAPI document and typed client regenerated and compared | no drift |
| Browser E2E | Playwright drives all 15 steps through the UI, one signed-in person per role, fresh database | passed (26.5 s) |
| Deployment smoke | `scripts/smoke_slice.py` against the Docker Compose stack (nginx → API → worker → sandbox) | passed in CI |
| Container images | API/worker and web images build | passed in CI |
| Dependency audit | `pip-audit`; `npm audit --audit-level=high --omit=dev` | no known Python vulnerabilities; two low-severity npm advisories (KaTeX via Mermaid) |
| Offline evaluations | `python -m agentic_sdlc.evals.run --all` against `evals/baselines.json` | all suites within baseline |

### Evaluation metrics (dataset 2026.10.1, rule-based strategies)

| Suite | Cases | Key results |
|---|---|---|
| requirements | 14 | statement recall 1.0, precision 1.0, locator validity 1.0, injection recall 1.0 with 0 false positives, ambiguity-term recall 1.0, **contradiction recall 0.5** |
| planning | 7 | requirement coverage 1.0, cycle detection 1.0 |
| patch_policy | 20 | block recall 1.0, allow rate 1.0 |
| release_policy | 16 | exact blocker match 1.0, unsafe releases 0 |
| redaction | 11 | secrets removed 1.0, benign text unchanged 1.0 |

The one failing case is kept in the dataset on purpose: "attachments ≤ 10 MB" vs "files up
to 25 MB" is a real contradiction the heuristic misses because the subjects use different
nouns. Two skill criteria are reported as **not measured offline**: the coding agent's
acceptance pass rate (covered only by the end-to-end tests) and token-estimate accuracy
(needs actuals from real model runs).

## Found and fixed during validation

| # | Finding | How it was found | Fix | Regression test |
|---|---|---|---|---|
| 1 | Platform git read `$HOME/.gitconfig` from the workspace, so a patch adding `.gitconfig` + `.gitattributes` could make git run a command on the platform | Security review; exploit reproduced | Private HOME and `GIT_CONFIG_GLOBAL` for every git process | `test_git_hardening.py` |
| 2 | Sandbox toolchain writable by the sandbox user on CI runners (inherited ACLs on `/opt`) | Isolation test failed in CI | Toolchain root-owned, ACLs stripped; sandbox refuses an unprotected toolchain | `test_sandbox.py` |
| 3 | Root worker could not clone origins created by the API user (git ignores `-c safe.directory` in local-clone upload-pack) | Compose smoke escalated at "implement" | `safe.directory` set in the platform git config | `test_clone_across_users` |
| 4 | Event stream held a database transaction for its whole life; the worker's first job stalled ~3 minutes behind `CREATE INDEX CONCURRENTLY` | Browser E2E timed out; worker stack captured with py-spy | Session released before streaming; checkpoint setup at worker start | `test_event_stream_releases_its_session` |
| 5 | Patch policy allowed weakening tests and committing PEM private keys | `patch_policy` evaluation | Blocks deleted test files, net test-function removal, new skip/xfail, PEM headers | `test_patch_review.py`, eval |
| 6 | In container images the sandbox masked the interpreter prefix instead of the source tree | Review of installed-package paths | Source root detected for checkouts and installed packages | Compose smoke |
| 7 | Release readiness showed "external CI: not configured" as a pass | Browser review | Shown as a warning | release-policy eval |

## Not verified here

- Model inference (no GPU or endpoint): the model path is exercised only with the mock provider.
- GitHub Actions deployment adapter, production deployment (disabled by policy) and the Docker
  sandbox backend.
- Pull-request creation by the Developer agent (not implemented; see the plan's gap table).

See [implementation-plan.md](implementation-plan.md) §3a for the full list of gaps against
the phase definitions.
