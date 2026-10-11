# Threat Model

Method: STRIDE over the trust boundaries below. Each threat lists the control and its
verification status. "Residual" lists what is not mitigated.

## Assets

Source documents (may contain confidential business information), requirements and design
artifacts, repository contents, credentials (Git tokens, deployment tokens, model API keys),
approval records, audit log, deployment targets.

## Trust boundaries

1. Browser ↔ API (authenticated users of varying roles).
2. API/worker ↔ untrusted content (uploaded documents, repository files, model output).
3. Worker ↔ sandbox (generated/untrusted code execution).
4. Platform ↔ external systems (Git hosting, CI/CD, model endpoints).

## Threats and controls

| # | Threat | Control | Status |
|---|---|---|---|
| T1 | Spoofed user / stolen token | Opaque tokens, sha256-hashed at rest, expiry + revocation; HTTPS required outside dev | Implemented; OIDC pending (ADR-0006) |
| T2 | Agent approves its own work | Approvals require a *human* principal holding the gate role; service accounts have no roles | Verified by tests |
| T3 | Approval for a different artifact than reviewed | Approval binds to content hash; changed artifact → request `stale`, decision 409 | Verified by tests |
| T4 | Cross-project data access | Every route resolves project membership; deny by default | Verified by tests |
| T5 | Prompt injection in documents | Content is passed to models only inside delimited data sections; system policy never sourced from documents; injection-like phrases flagged as warnings; extracted items must quote text that exists at the cited locator | Verified (heuristic + locator check) |
| T6 | Malicious DOCX (zip bomb, XXE, path traversal) | Size limit, zip entry count/uncompressed-size limits, magic check, `python-docx` (lxml with entity resolution disabled), no archive extraction to disk | Verified by tests |
| T7 | Generated code escapes / exfiltrates | Sandbox: separate network namespace (no network), PID namespace, non-root uid, `no_new_privs`, all capabilities dropped, masked secret paths, scrubbed environment, CPU/memory/file/process limits, wall-clock timeout | Verified by tests (namespace backend) |
| T8 | Sandbox reads host files | Namespace backend masks configured paths with tmpfs but **does not pivot the root filesystem**; world-readable host files remain readable | **Residual** — use Docker backend (read-only image rootfs) in real deployments |
| T9 | Path traversal via patches / filenames | Patch paths validated (no absolute, no `..`, no `.git/`); upload filenames never used as paths | Verified by tests |
| T10 | Secret leakage to logs/events/prompts | Redaction filter on logs and tool-invocation arguments; secrets never placed in model prompts or sandbox env | Verified for redaction; review-based elsewhere |
| T11 | Tampering with audit log | DB trigger rejects UPDATE/DELETE on `audit_events` | Verified by tests |
| T12 | Unauthorized deployment | Production adapter disabled by default; release gate requires `release_approver`; deploy adapter refuses without an approved, non-stale decision | Verified by tests |
| T13 | Resource exhaustion (runaway loops, cost) | Bounded repair/revision loops; per-run token/cost/call/time budgets; per-token API rate limiting | Verified by tests (loops, budgets, rate limit) |
| T14 | Replayed / duplicate commands | Job dedup keys; single active run per project; spurious resumes do not advance a gate | Verified by tests |
| T15 | Supply chain | Pinned dependency ranges; `pip-audit` and `npm audit --audit-level=high` in CI | Running in CI; two low-severity npm advisories open (KaTeX via Mermaid, no fix without a Mermaid downgrade) |
| T16 | Privileged skill modification by untrusted input | Skills load only from the repository `skills/` directory at startup; no API to install skills | Verified by design (no endpoint) |
| T17 | Repository content configures platform git (e.g. a committed `.gitconfig` + `.gitattributes` filter runs a command on `git add`) | Every platform git process uses a private HOME and `GIT_CONFIG_GLOBAL` written by the platform; system config ignored; hooks disabled | Verified by tests (`test_git_hardening.py`); found during Phase 8 review, exploit reproduced on the earlier code |
| T18 | Sandboxed code modifies the sandbox toolchain to persist into later runs | Toolchain owned by root, no group/other write, no POSIX ACLs; the sandbox refuses to start otherwise | Verified by tests; found when CI runners granted write via inherited ACLs |
| T19 | Patch weakens the test suite to pass verification | Patch policy rejects deleted test files, net removal of test functions and new skip/xfail markers; acceptance tests are owned by QA, not the developer agent | Verified by tests and the `patch_policy` evaluation |

## Residual risks (honest list)

- Namespace sandbox shares the host root filesystem read-only-by-permission (T8).
- In-memory rate limiter is per-process.
- Dev authentication mode seeds known tokens; it refuses to start when `APP_ENV=production`.
- Heuristic injection detection is a signal, not a guarantee; the real control is that
  documents cannot change policy, tools or permissions.
- GitHub Actions deployment adapter is not exercised in this environment.
- Contradiction detection is heuristic (0.5 recall on the evaluation set): different nouns for
  the same subject are not linked.
