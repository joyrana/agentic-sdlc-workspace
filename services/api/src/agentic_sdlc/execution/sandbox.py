"""Sandbox backends for untrusted code (ADR-0005).

`NamespaceSandbox` (verified here): separate mount, network and PID namespaces; tmpfs masks over
sensitive paths (configured paths plus the platform's own data and source directories); drop to a
dedicated non-root uid with no_new_privs, no supplementary groups and no capabilities; rlimits for
address space, CPU time, processes, file size and open files; wall-clock timeout that kills the whole
process tree; scrubbed environment. The workspace is the only writable location.

`DockerSandbox`: --network none, read-only root, non-root user, all capabilities dropped,
no-new-privileges, pids/memory/cpu limits, only the workspace mounted. Verified in CI only.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from agentic_sdlc.core.logging import redact
from agentic_sdlc.core.settings import REPO_ROOT, Settings, get_settings

MAX_CAPTURE = 64 * 1024


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    backend: str
    limits: dict[str, int] = field(default_factory=dict)
    command: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        d = asdict(self)
        d.pop("stdout")
        d.pop("stderr")
        return d


class SandboxError(Exception):
    pass


class SandboxBackend(Protocol):
    name: str

    def run(
        self,
        command: list[str],
        workdir: Path,
        *,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult: ...


def _tail(path: Path) -> str:
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - MAX_CAPTURE))
        data = fh.read()
    text = data.decode("utf-8", errors="replace")
    return redact(("[…truncated…]\n" if size > MAX_CAPTURE else "") + text)


def ensure_traversable(path: Path) -> None:
    """Grant search (o+x, never read) on `path` and its ancestors so the sandbox uid can reach the
    workspace. Listing stays forbidden; only the workspace itself is owned by the sandbox uid."""
    for p in [path, *path.parents]:
        if p == Path("/"):
            break
        mode = p.stat().st_mode
        if not mode & 0o001:
            os.chmod(p, (mode & 0o7777) | 0o001)


def _base_env(workdir: Path, settings: Settings, extra: dict[str, str] | None) -> dict[str, str]:
    toolchain_bin = str(Path(settings.sandbox_python).parent)
    env = {
        "PATH": f"{toolchain_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": str(workdir / ".tmp"),
    }
    for k, v in (extra or {}).items():
        if k.upper() in ("PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME") or not k.isidentifier():
            raise SandboxError(f"environment variable {k} is not allowed")
        env[k] = v
    return env


def _source_root(settings: Settings) -> Path:
    """The platform's own source tree. In a checkout that is the repository root; in an installed
    package (container image) REPO_ROOT points into the interpreter prefix, so use the directory
    that holds the skills instead."""
    if (REPO_ROOT / "skills").is_dir() and (REPO_ROOT / "services").is_dir():
        return REPO_ROOT
    return settings.skills_dir.resolve().parent


def toolchain_problems(settings: Settings) -> list[str]:
    """The sandbox interpreter must not be modifiable by the sandbox user: a writable toolchain
    would let one run plant code that a later run (or the platform's test evidence) executes."""
    python = Path(settings.sandbox_python)
    problems = []
    for d in {python.resolve().parent, python.resolve().parent.parent, python.parent, python.parent.parent}:
        try:
            st = d.stat()
        except OSError:
            continue
        if st.st_uid == settings.sandbox_uid:
            problems.append(f"{d} is owned by the sandbox user")
        if st.st_mode & 0o002 or (st.st_mode & 0o020 and st.st_gid == settings.sandbox_uid):
            problems.append(f"{d} is writable by the sandbox user (mode {oct(st.st_mode & 0o777)})")
        try:
            # Mode bits do not show POSIX ACL grants; any extended ACL is treated as unsafe.
            if os.getxattr(d, "system.posix_acl_access"):
                problems.append(f"{d} carries POSIX ACLs (remove them with `setfacl -b`)")
        except OSError:
            pass
    return sorted(problems)


def _masked_paths(settings: Settings, workdir: Path) -> list[str]:
    candidates = [*settings.sandbox_masked_paths, str(settings.data_dir), str(_source_root(settings))]
    out = []
    for c in candidates:
        p = Path(c).resolve()
        if not p.exists() or not p.is_dir():
            continue
        if workdir.resolve().is_relative_to(p) or Path(settings.sandbox_python).resolve().is_relative_to(p):
            raise SandboxError(f"masked path {p} would hide the workspace or the sandbox toolchain")
        out.append(str(p))
    return sorted(set(out))


SCRATCH = ("/tmp", "/var/tmp", "/dev/shm")  # noqa: S108 - these are masked with private tmpfs


def _scratch_paths(workdir: Path) -> list[str]:
    """Shared temp dirs get a private, empty tmpfs unless the workspace lives inside them."""
    return [p for p in SCRATCH if Path(p).is_dir() and not workdir.is_relative_to(Path(p).resolve())]


INNER = """#!/bin/sh
# Runs as root inside fresh mount/net/pid namespaces, masks paths, then drops privileges for good.
set -e
for p in {masks}; do
  if [ -d "$p" ]; then mount -t tmpfs -o size=1m,mode=000 tmpfs "$p"; fi
done
for p in {scratch}; do
  if [ -d "$p" ]; then mount -t tmpfs -o size=64m,mode=1777 tmpfs "$p"; fi
done
exec setpriv --reuid={uid} --regid={uid} --clear-groups --no-new-privs --inh-caps=-all --bounding-set=-all \\
  prlimit --as={mem} --cpu={cpu} --nproc={nproc} --fsize={fsize} --nofile=256 -- "$@"
"""


class NamespaceSandbox:
    name = "namespace"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def limits(self) -> dict[str, int]:
        s = self.settings
        return {
            "memory_bytes": s.sandbox_memory_bytes,
            "cpu_seconds": s.sandbox_cpu_seconds,
            "max_processes": s.sandbox_max_processes,
            "max_file_bytes": s.sandbox_max_file_bytes,
            "timeout_s": s.sandbox_timeout_s,
            "uid": s.sandbox_uid,
        }

    def prepare_workdir(self, workdir: Path) -> None:
        """Give the sandbox uid ownership of the workspace (its only writable location)."""
        (workdir / ".tmp").mkdir(exist_ok=True)
        for root, dirs, files in os.walk(workdir):
            for name in [*dirs, *files]:
                os.lchown(os.path.join(root, name), self.settings.sandbox_uid, self.settings.sandbox_uid)
        os.chown(workdir, self.settings.sandbox_uid, self.settings.sandbox_uid)
        ensure_traversable(workdir.parent)

    def run(
        self,
        command: list[str],
        workdir: Path,
        *,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        s = self.settings
        if os.geteuid() != 0:
            raise SandboxError("the namespace sandbox requires root or CAP_SYS_ADMIN for the worker")
        workdir = workdir.resolve()
        problems = toolchain_problems(s)
        if problems:
            raise SandboxError("refusing to run: sandbox toolchain is not protected: " + "; ".join(problems))
        masks = _masked_paths(s, workdir)
        self.prepare_workdir(workdir)
        timeout = timeout_s or s.sandbox_timeout_s
        with tempfile.TemporaryDirectory(prefix="asw-sbx-ctl-") as ctl:
            ctl_dir = Path(ctl)
            os.chmod(ctl_dir, 0o700)
            inner = ctl_dir / "inner.sh"
            inner.write_text(
                INNER.format(
                    masks=" ".join(shlex.quote(m) for m in masks) or "''",
                    scratch=" ".join(shlex.quote(p) for p in _scratch_paths(workdir)) or "''",
                    uid=s.sandbox_uid,
                    mem=s.sandbox_memory_bytes,
                    cpu=s.sandbox_cpu_seconds,
                    nproc=s.sandbox_max_processes,
                    fsize=s.sandbox_max_file_bytes,
                )
            )
            inner.chmod(0o700)
            out_path, err_path = ctl_dir / "stdout", ctl_dir / "stderr"
            argv = [
                "unshare",
                "--mount",
                "--net",
                "--pid",
                "--fork",
                "--mount-proc",
                "--propagation",
                "private",
                str(inner),
                *command,
            ]
            start = time.monotonic()
            timed_out = False
            with out_path.open("wb") as out, err_path.open("wb") as err:
                proc = subprocess.Popen(
                    argv,
                    cwd=workdir,
                    env=_base_env(workdir, s, env),
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            return SandboxResult(
                exit_code=proc.returncode,
                stdout=_tail(out_path),
                stderr=_tail(err_path),
                duration_s=round(time.monotonic() - start, 3),
                timed_out=timed_out,
                backend=self.name,
                limits=self.limits(),
                command=command,
            )


class DockerSandbox:
    """Container backend for deployments where images can be pulled (verified in CI only)."""

    name = "docker"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def run(
        self,
        command: list[str],
        workdir: Path,
        *,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        s = self.settings
        uid = s.sandbox_uid
        NamespaceSandbox(s).prepare_workdir(workdir.resolve())
        env_args = [
            a
            for k, v in _base_env(Path("/workspace"), s, env).items()
            if k != "PATH"
            for a in ("-e", f"{k}={v}")
        ]
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            f"{uid}:{uid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(s.sandbox_max_processes),
            "--memory",
            str(s.sandbox_memory_bytes),
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,size=64m",  # noqa: S108 - container-private tmpfs
            "-v",
            f"{workdir.resolve()}:/workspace:rw",
            "-w",
            "/workspace",
            *env_args,
            s.sandbox_docker_image,
            *command,
        ]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, timeout=timeout_s or s.sandbox_timeout_s, check=False
            )
            return SandboxResult(
                proc.returncode,
                redact(proc.stdout.decode(errors="replace")[-MAX_CAPTURE:]),
                redact(proc.stderr.decode(errors="replace")[-MAX_CAPTURE:]),
                round(time.monotonic() - start, 3),
                False,
                self.name,
                {},
                command,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                -9, "", "timed out", round(time.monotonic() - start, 3), True, self.name, {}, command
            )


def get_sandbox(settings: Settings | None = None) -> SandboxBackend:
    s = settings or get_settings()
    if s.sandbox_backend == "namespace":
        return NamespaceSandbox(s)
    if s.sandbox_backend == "docker":
        return DockerSandbox(s)
    raise SandboxError("no sandbox backend configured; refusing to execute untrusted code")
