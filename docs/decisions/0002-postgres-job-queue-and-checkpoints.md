# ADR-0002: PostgreSQL for job queue, event log and LangGraph checkpoints

- Status: Accepted
- Date: 2026-10-10

## Context

Workflows must be durable, resumable after restart, and idempotent. The spec allows Redis
"only where needed".

## Decision

- LangGraph checkpoints: `langgraph-checkpoint-postgres` (`PostgresSaver`) in the same database.
  Verified in this environment: an interrupted graph resumed correctly from a new connection
  and a new graph instance without re-executing completed nodes.
- Job queue: table `workflow_jobs` claimed with `SELECT … FOR UPDATE SKIP LOCKED`, with a
  unique `dedup_key`, attempt counter and lease expiry (stale leases are reclaimed).
- Event log: append-only `workflow_events` with a monotonically increasing `seq` used as the
  SSE `Last-Event-ID`.

## Consequences

No additional infrastructure. Throughput is bounded by Postgres, which is ample for workflow
orchestration volumes. Redis can be introduced behind the queue interface if needed.
