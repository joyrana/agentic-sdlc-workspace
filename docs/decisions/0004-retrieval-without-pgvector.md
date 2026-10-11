# ADR-0004: Defer pgvector; use structured locators and Postgres full-text search

- Status: Accepted
- Date: 2026-10-10

## Context

The spec prefers pgvector "where justified". The extension is not installable in the build
environment, and the first slice's authoritative relationships (source → requirement →
criterion → backlog → code → test → release) must not depend on vector similarity anyway.

## Decision

- Traceability uses explicit foreign keys and link tables only.
- Source passages are located by `(document_version_id, block_id)` plus verbatim text.
- Search over normalized document blocks uses Postgres full-text search (`tsvector`, GIN).
- A `Retriever` interface exists so a pgvector-backed hybrid retriever can be added when the
  extension is available and an evaluation shows it improves recall.

## Consequences

No semantic retrieval yet. Documented in Known Limitations.
