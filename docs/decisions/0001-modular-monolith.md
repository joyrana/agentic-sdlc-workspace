# ADR-0001: Modular monolith with a separate worker and sandbox boundary

- Status: Accepted
- Date: 2026-10-10

## Context

The master specification lists 17 logical capabilities and warns against a microservice per
agent. The first goal is an end-to-end vertical slice that is easy to run and test.

## Decision

One Python package `agentic_sdlc` (under `services/api`) with internal modules per capability
(see `docs/architecture/system-overview.md`). Two process roles from the same code:

- `api` — FastAPI, serves HTTP and SSE, enqueues jobs, never executes workflows.
- `worker` — claims jobs from Postgres and executes LangGraph graphs.

Untrusted code runs only through `agentic_sdlc.execution` sandbox backends, a separate OS
process with its own namespaces and limits.

## Consequences

- One deployable, one migration history, one test suite.
- Module boundaries are conventions enforced by review; extracting `execution` into its own
  service later is planned if isolation or scaling demands it.
- The recommended tree's `services/orchestrator`, `agent-runtime`, `document-service`,
  `execution-service` exist as modules, not separate services. Empty directories were not
  created to mirror the tree.
