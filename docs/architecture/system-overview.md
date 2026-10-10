# System Overview

## Shape

A **modular monolith** (one Python package, `agentic_sdlc`) serves the API and runs workflows
in a separate **worker** process. Untrusted code never runs in either: it runs in a
**sandbox** process boundary. The React workspace is a pure client of the versioned REST API
and the server-sent event stream.

```mermaid
flowchart LR
  subgraph Browser
    W[Web workspace<br/>React + Fluent UI v9]
  end
  subgraph Platform["agentic_sdlc (modular monolith)"]
    API[FastAPI /api/v1<br/>auth, RBAC, rate limit,<br/>correlation IDs]
    SVC[Domain services<br/>projects · documents · requirements<br/>backlog · architecture · approvals<br/>verification · QA · releases]
    SK[Skill runtime<br/>manifests · schemas · tool permissions<br/>budgets · retries]
    MR[Model registry & router<br/>mock · OpenAI-compatible (vLLM/Qwen)]
    WK[Worker<br/>LangGraph delivery & intake graphs]
    AUD[Audit + events + traces]
  end
  subgraph Isolation
    SB[Sandbox runner<br/>namespaces: no network, non-root,<br/>masked paths, rlimits]
  end
  PG[(PostgreSQL<br/>records · checkpoints · job queue · events)]
  OS[(Object storage<br/>content-addressed)]
  GIT[(Fixture / Git repositories)]

  W -- REST + SSE --> API
  API --> SVC
  SVC --> PG
  SVC --> OS
  API -- enqueue job --> PG
  WK -- claim job --> PG
  WK --> SK --> MR
  SK --> SVC
  SK -- commands --> SB
  SB --> GIT
  SVC --> AUD --> PG
```

## Module boundaries

| Module (`agentic_sdlc.*`) | Responsibility |
|---|---|
| `core` | settings, database session, IDs, hashing, errors, logging, tracing, object storage |
| `auth` | token authentication, principals, role → permission policy (deny by default) |
| `audit` | immutable audit events (DB trigger forbids UPDATE/DELETE) |
| `events` | typed event envelope, persistence, SSE stream |
| `projects` | projects, memberships, repositories |
| `documents` | upload validation, normalization of DOCX/transcripts into located blocks |
| `requirements` | requirements, business rules, acceptance criteria, open questions, revisions |
| `backlog` | hierarchy, dependencies (cycle-checked), estimates vs actuals |
| `skills` | manifest model, registry, runtime, tool gateway, implementations |
| `models` | model registry, providers, router, token accounting, budgets |
| `architecture` | proposals, ADRs, content-hashed revisions |
| `approvals` | approval requests/decisions, stale detection, gate policy |
| `execution` | sandbox backends, repository workspaces, coding agents |
| `quality` | test runs, BA verification, defects, quality gates |
| `releases` | readiness, releases, deployment adapters, smoke/health checks |
| `workflows` | LangGraph graphs, run lifecycle, job queue, worker |
| `api` | HTTP routers; thin, no business logic |

Rules enforced by review and tests:

- Routers call services; services own transactions; graphs call services and skills.
- Only the `skills` runtime may call a model provider, and only through the router.
- Only `execution` may start subprocesses, and only through a sandbox backend.
- The UI never decides a workflow transition; it posts commands and renders state.

## Data flow for the vertical slice

1. Upload → `documents` validates type/size/structure, stores the original (sha256-addressed),
   creates a `document_version`, enqueues an **intake** job.
2. Worker runs the intake graph: normalize → `brd-analysis`/`transcript-analysis` →
   requirements, business rules, criteria, open questions, each with a locator into the
   normalized blocks.
3. A human reviews and edits criteria, resolves questions, then starts a **delivery** run.
   The run snapshots the requirement and criteria versions it consumes.
4. The delivery graph (see `agent-workflows.md`) plans, analyzes the repository, proposes an
   architecture, and **suspends** for approval. Approval decisions are validated by the API,
   recorded, and only then resume the graph.
5. The developer stage applies changes in a disposable clone; QA runs tests in the sandbox;
   BA verification maps criteria to evidence; failures create defects and loop (bounded).
6. Release readiness is computed from evidence, a release approval gates staging, the local
   staging adapter deploys and runs smoke/health checks; production is disabled by policy.

## Persistence

PostgreSQL holds all authoritative state: domain records, LangGraph checkpoints
(`langgraph-checkpoint-postgres`), the job queue (`SELECT … FOR UPDATE SKIP LOCKED`), the
event log, token usage and immutable audit events. Original documents, normalized
representations and build artifacts go to the object storage abstraction (local filesystem
backend, content-addressed by sha256).

## Identity of artifacts

Every persisted artifact has a UUID, `project_id`, timestamps, an integer `version` and, for
reviewable artifacts, a `content_hash` (sha256 of canonical JSON). Approvals bind to
`(artifact_type, artifact_id, version, content_hash)`.
