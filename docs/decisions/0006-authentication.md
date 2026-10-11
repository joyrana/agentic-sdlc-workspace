# ADR-0006: Token authentication now, OIDC later; RBAC policy in the backend

- Status: Accepted
- Date: 2026-10-10

## Context

Approvals must be authenticated and attributable. No identity provider is specified.

## Decision

- Bearer tokens (random 32 bytes, URL-safe), stored as sha256 hashes with expiry/revocation.
- `APP_ENV=development` may seed one user per role with documented tokens; startup refuses dev
  seeding when `APP_ENV=production`.
- Roles (per project membership): `project_owner`, `engineer`, `reviewer`, `qa`,
  `release_approver`, `auditor`; global `admin`.
- A static permission table maps roles to permissions; every endpoint declares the
  permission it needs; absence of a grant is a denial.
- Service accounts (agents) authenticate internally and hold no roles.

## Consequences

The authentication boundary (`Principal` resolution) is isolated so an OIDC resolver can
replace token lookup without touching authorization.
