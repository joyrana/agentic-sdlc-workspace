# Implementation Plan and Phase Status

This file is the source of truth for what exists, what is verified, and what is next.
It is updated at the end of every increment. Status values mean:

- **Verified** — implemented, exercised by automated tests that pass in this repository.
- **Implemented, unverified here** — code exists but could not be exercised in the build
  environment (reason stated).
- **Not started** — nothing exists yet.

## 1. Repository assessment (Phase 0, 2026-10-10)

| Item | Finding |
|---|---|
| Repository state | New. `main` had no commits, no files, no CI, no prior decisions. Nothing to preserve. |
| Build environment | Linux x86_64, 2 vCPU, 7 GB RAM, **no GPU**. Python 3.13.16, Node 22.22.0, PostgreSQL 16.15 server binaries, Docker 29.8 daemon. |
| Network constraints | PyPI, npm and GitHub reachable. **Docker Hub blocked** (no container images can be pulled). Hugging Face model downloads not attempted (no GPU to run them). |
| pgvector | Not installable (no `postgresql-16-pgvector` package available). See ADR-0004. |
| Sandbox options | Docker images unavailable; Linux namespaces (`unshare`), `setpriv`, `prlimit` available and verified. See ADR-0005. |

### Consequences of the environment

1. **Real LLM inference cannot run here.** The provider-neutral model layer, an
   OpenAI-compatible adapter (for vLLM serving Qwen) and a deterministic mock provider are
   implemented. Skills that would use an LLM have a deterministic *heuristic* strategy, and
   every result records which strategy produced it (`heuristic`, `llm`, `mock`, `scripted`).
   The UI shows this label. Nothing heuristic is presented as model output.
2. **Coding agent.** Without a code model, the Developer agent uses a *scripted* coding agent
   that replays fixture patches for registered fixture repositories only. It is labeled as
   such everywhere. The LLM coding agent path (diff in structured output → `git apply --check`
   in the sandbox) is implemented and tested with the mock provider.
3. **Sandbox.** Namespace sandbox is the verified backend. The Docker backend is implemented
   but can only be exercised where images can be pulled (GitHub Actions).

## 2. First vertical slice (the scope of this build)

The 15-step journey from the master prompt §26, mapped to concrete mechanisms:

| # | Step | Mechanism |
|---|---|---|
| 1 | Create a project | `POST /api/v1/projects` (RBAC: any authenticated user becomes project owner) |
| 2 | Upload BRD (.docx) | `POST /api/v1/projects/{id}/documents` → validation → object storage → intake workflow |
| 3 | Extract requirements + sources | `brd-analysis` skill; every requirement carries a block locator and verbatim source text |
| 4 | Review/edit acceptance criteria | `PATCH /acceptance-criteria/{id}` with optimistic concurrency; revisions stored |
| 5 | Epic + dependent stories | `backlog-decomposition` + `effort-estimation` + `dependency-analysis` skills inside the delivery workflow |
| 6 | Inspect sample repository | `repository-exploration` skill over a read-only clone |
| 7 | Architecture proposal + ADR | `architecture-design` + `adr-generation` skills; content-hashed revisions |
| 8 | Human approval | LangGraph `interrupt()`; approval API validates role + content hash, then resumes |
| 9 | Change in isolated repo | Developer agent in a disposable clone, executed via the sandbox |
| 10 | Automated tests | pytest inside the sandbox, JUnit XML parsed into a test run record |
| 11 | Verify against criteria | BA verification maps criteria → independent acceptance tests → evidence |
| 12 | Defect + bounded repair | QA creates linked defects; repair loop limited by `max_repair_attempts` |
| 13 | Release readiness report | `release-readiness` skill computes eligibility from recorded evidence only |
| 14 | Approval before staging | `release_staging` approval gate (role `release_approver`) |
| 15 | Record evidence | Release, deployment, smoke/health results, audit events |

## 3. Phase status

| Phase | Status | Evidence |
|---|---|---|
| 0 Assessment & plan | Verified | This document, architecture docs, threat model, ADRs |
| 1 Workspace foundation | Verified (Docker images CI-only) | `tests/integration/test_projects_api.py`; live API + Vite proxy check |
| 2 Documents & BA | Verified (heuristic strategy; LLM path via mock only) | `tests/integration/test_documents_requirements.py`, `tests/unit/test_extraction_rules.py`, `tests/security/test_upload_validation.py`, `tests/contract/test_skill_runtime.py` |
| 3 Skills, planner, model registry | Verified (heuristic planning; router unit-tested) | `tests/unit/test_graph_and_planning.py`, `tests/unit/test_model_router.py`, `tests/integration/test_backlog.py` |
| 4 Architect & approvals | Verified (backend; UI pending) | `tests/workflow/test_architecture_and_approvals.py` incl. resume in a separate worker process |
| 5 Developer agent | Verified (scripted coding agent for the sample; LLM path via mock) | `tests/security/test_sandbox.py` (11 isolation tests), `tests/unit/test_patch_review.py`, `tests/workflow/test_developer_agent.py` |
| 6 BA verification & QA | Not started | — |
| 7 Release management | Not started | — |
| 8 Evaluation & optimization | Not started | — |

## 4. Assumptions

- A single organization per deployment for the first slice (multi-tenancy deferred; every
  record is project-scoped so tenant scoping can be added above projects).
- Development authentication uses opaque bearer tokens stored hashed. OIDC is the intended
  production mechanism and is not implemented yet (ADR-0006).
- Workflow execution uses a Postgres-backed job queue and a worker process; no Redis.
- Sample data (BRD, fixture repository, fixture patches) is labeled `sample` in the database
  and the UI.

## 5. Open decisions (need a human)

- Production identity provider (OIDC issuer, group → role mapping).
- Which Qwen model(s) and serving hardware to standardize on; license review per model card.
- Target deployment platform for real staging/production (the slice ships a local staging
  adapter and a GitHub Actions dispatch adapter that is disabled until configured).
