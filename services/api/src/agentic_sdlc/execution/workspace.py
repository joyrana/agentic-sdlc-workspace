"""Disposable git workspaces (platform-only) and patch policy checks.

Two kinds of directories, deliberately separate:
- A *git workspace* is a clone used by the platform to apply a validated patch, commit and push the
  agent branch. It is never exposed to the sandbox, so sandboxed code cannot plant git hooks or
  config that a later git command (run by the platform) would execute.
- A *test tree* (see test_runner) is an export of one commit without `.git`, handed to the sandbox.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_sdlc.core.errors import ValidationFailed
from agentic_sdlc.core.gitenv import git_env
from agentic_sdlc.core.logging import _SECRET_PATTERNS, redact
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.execution.repo_reader import safe_relpath
from agentic_sdlc.execution.sandbox import ensure_traversable

MAX_PATCH_BYTES = 1_000_000
GIT_SAFE = [
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "safe.directory=*",
    "-c",
    "protocol.file.allow=always",
    "-c",
    "commit.gpgsign=false",
]
AGENT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Developer Agent",
    "GIT_AUTHOR_EMAIL": "agent-developer@agentic-sdlc.invalid",
    "GIT_COMMITTER_NAME": "Agentic SDLC Workspace",
    "GIT_COMMITTER_EMAIL": "platform@agentic-sdlc.invalid",
}
RISKY = {
    r"\bsubprocess\b|\bos\.system\b|\bos\.popen\b": "starts processes",
    r"\beval\(|\bexec\(": "dynamic code execution",
    r"\bsocket\b|\brequests\b|\burllib\b|\bhttpx\b": "network access",
    r"\bpickle\.loads?\(": "unsafe deserialization",
}


def git(cwd: Path, *args: str, timeout: int = 120, input_text: str | None = None) -> str:
    env = git_env(**AGENT_IDENTITY)
    proc = subprocess.run(
        ["git", *GIT_SAFE, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=input_text,
    )
    if proc.returncode != 0:
        last = redact(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")[:300]
        raise ValidationFailed(
            f"git {args[0]} failed: {last}", details={"stderr": redact(proc.stderr[-800:])}
        )
    return proc.stdout


@dataclass
class PatchReview:
    files: list[str] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "added_lines": self.added_lines,
            "removed_lines": self.removed_lines,
            "violations": self.violations,
            "warnings": self.warnings,
            "ok": self.ok,
        }


# The multi-line PEM pattern cannot match a diff line by line; the header alone is enough to block.
PEM_HEADER = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TEST_DEF = re.compile(r"^\s*(async\s+)?def\s+test_\w*\s*\(")
SKIP_MARK = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.skip\(|\bunittest\.skip")


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    in_tests_dir = path.startswith("tests/") or "/tests/" in path
    return in_tests_dir or name.startswith("test_") or name.endswith("_test.py")


def review_patch(diff: str, *, allowed_files: set[str] | None = None) -> PatchReview:
    """Deterministic policy review of a unified diff before it touches any repository.

    Blocks unsafe paths, binaries, symlinks, credentials, out-of-plan files, and changes that weaken
    the test suite (deleting test files, net removal of test functions, adding skip/xfail markers).
    """
    r = PatchReview()
    if len(diff.encode()) > MAX_PATCH_BYTES:
        r.violations.append("patch exceeds 1 MB")
        return r
    if not diff.strip():
        r.violations.append("patch is empty")
        return r
    current: str | None = None
    tests_removed: dict[str, int] = {}
    tests_added: dict[str, int] = {}
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"^diff --git a/(\S+) b/(\S+)$", line)
            if not m:
                r.violations.append(f"unparseable diff header: {line[:120]}")
                current = None
                continue
            current = m.group(2)
            for p in {m.group(1), m.group(2)}:
                try:
                    safe_relpath(p)
                except ValidationFailed:
                    r.violations.append(f"unsafe path: {p[:120]}")
            if m.group(2) not in r.files:
                r.files.append(m.group(2))
        elif line.startswith(("GIT binary patch", "Binary files ")):
            r.violations.append("binary patches are not accepted")
        elif re.match(r"^(new|deleted) file mode 1(2|6)0000|^(old|new) mode 1(2|6)0000", line):
            r.violations.append("symlinks and submodules are not accepted")
        elif line.startswith("deleted file mode") and current and _is_test_path(current):
            r.violations.append(f"deletes test file {current}")
        elif line.startswith(("rename to ", "copy to ")):
            try:
                safe_relpath(line.split(" ", 2)[2])
            except ValidationFailed:
                r.violations.append(f"unsafe rename target: {line[:120]}")
        elif line.startswith("+") and not line.startswith("+++"):
            r.added_lines += 1
            added = line[1:]
            if current and _is_test_path(current):
                if TEST_DEF.match(added):
                    tests_added[current] = tests_added.get(current, 0) + 1
                if SKIP_MARK.search(added):
                    r.violations.append(f"adds a skip/xfail marker in {current}")
            for pat in (*_SECRET_PATTERNS, PEM_HEADER):
                if pat.search(added):
                    r.violations.append("added line looks like a credential")
                    break
            for risky, what in RISKY.items():
                if re.search(risky, added):
                    msg = f"added code {what}"
                    if msg not in r.warnings:
                        r.warnings.append(msg)
        elif line.startswith("-") and not line.startswith("---"):
            r.removed_lines += 1
            if current and _is_test_path(current) and TEST_DEF.match(line[1:]):
                tests_removed[current] = tests_removed.get(current, 0) + 1
    for path, n in tests_removed.items():
        if n > tests_added.get(path, 0):
            r.violations.append(f"removes {n - tests_added.get(path, 0)} test function(s) from {path}")
    if allowed_files is not None:
        outside = [f for f in r.files if f not in allowed_files and not f.startswith("tests/")]
        if outside:
            r.violations.append(f"changes files outside the approved plan: {', '.join(sorted(outside))}")
    return r


@dataclass
class GitWorkspace:
    root: Path

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def new_dir(prefix: str) -> Path:
    root = get_settings().workspaces_dir
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o711)  # noqa: S103 - traverse only; listing stays forbidden
    ensure_traversable(root)
    d = root / f"{prefix}-{uuid.uuid4().hex[:12]}"
    d.mkdir(mode=0o711)
    return d


def open_workspace(origin: Path, ref: str) -> GitWorkspace:
    ws = GitWorkspace(new_dir("git"))
    git(ws.root, "clone", "--quiet", "--no-hardlinks", str(origin), "repo", timeout=300)
    git(ws.repo, "checkout", "--quiet", "--detach", ref)
    return ws


def apply_and_commit(ws: GitWorkspace, diff: str, message: str) -> str:
    git(ws.repo, "apply", "--check", "--whitespace=nowarn", "-", input_text=diff)
    git(ws.repo, "apply", "--index", "--whitespace=nowarn", "-", input_text=diff)
    git(ws.repo, "commit", "--quiet", "--no-verify", "-m", message)
    return ws.head()


def push_branch(ws: GitWorkspace, branch: str) -> None:
    if not re.match(r"^agent/[a-z0-9-]{4,60}$", branch):
        raise ValidationFailed("agent branches must be under agent/")
    git(ws.repo, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")


def diff_between(origin: Path, base: str, head: str) -> tuple[str, list[str]]:
    diff = git(origin, "diff", "--no-color", "--no-ext-diff", f"{base}..{head}")
    files = [f for f in git(origin, "diff", "--name-only", f"{base}..{head}").splitlines() if f]
    return diff, files
