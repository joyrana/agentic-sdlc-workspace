# ADR-0005: Sandbox backends — Linux namespaces (verified) and Docker (for deployments)

- Status: Accepted
- Date: 2026-10-10

## Context

Generated code must run isolated with no network, no credentials, non-root, resource-limited.
Docker Hub is blocked in the build environment, so container images cannot be pulled.

## Decision

`SandboxBackend` interface with two implementations:

1. **`namespace`** (default here, verified): `unshare --mount --net --pid --fork --mount-proc`;
   tmpfs masks over configured sensitive paths; `setpriv` to a dedicated non-root uid with
   `--no-new-privs`, cleared groups and all inheritable capabilities dropped; `prlimit` for
   address space, CPU seconds, processes, file size and open files; wall-clock timeout that
   kills the process group; environment scrubbed to an allow-list. Requires root (or
   `CAP_SYS_ADMIN`) for the platform worker.
2. **`docker`** (implemented, verified only in CI): `docker run --network none --read-only
   --user 64000 --cap-drop ALL --security-opt no-new-privileges --pids-limit --memory --cpus`
   with only the workspace mounted read-write.

## Consequences

The namespace backend does not hide world-readable host files (it does not pivot root); this
is a recorded residual risk (threat model T8). Production deployments should use the Docker
backend or a stronger isolation runtime (gVisor, Firecracker).
