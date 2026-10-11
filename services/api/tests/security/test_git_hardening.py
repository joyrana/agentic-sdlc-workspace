"""Git runs on the platform side only, so repository content must never configure it."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from agentic_sdlc.core.gitenv import git_env
from agentic_sdlc.execution.workspace import apply_and_commit, git, open_workspace


def _origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "app.py").write_text("x = 1\n")
    git(origin, "init", "-q", "-b", "main")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "init")
    return origin


def _new_file(path: str, body: str) -> str:
    lines = body.splitlines()
    added = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{added}"
    )


def test_committed_gitconfig_cannot_run_filters(tmp_path: Path) -> None:
    marker = Path("/tmp") / f"asw-pwned-{uuid.uuid4().hex}"
    attack = _new_file(".gitattributes", "*.py filter=pwn") + _new_file(
        ".gitconfig", f'[filter "pwn"]\n\tclean = touch {marker}\n\tsmudge = touch {marker}'
    )
    ws = open_workspace(_origin(tmp_path), "main")
    try:
        apply_and_commit(ws, attack, "plant config")
        follow_up = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
        apply_and_commit(ws, follow_up, "trigger filters")
        git(ws.repo, "checkout", "-q", "HEAD~1")
        assert not marker.exists(), "a filter from repository content was executed"
    finally:
        ws.cleanup()
        marker.unlink(missing_ok=True)


def test_home_and_global_config_are_platform_private() -> None:
    env = git_env()
    home = Path(env["HOME"])
    assert env["GIT_CONFIG_GLOBAL"] == str(home / ".gitconfig")
    assert oct(home.stat().st_mode & 0o777) == "0o700"
    assert "hooksPath = /dev/null" in (home / ".gitconfig").read_text()


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to create a repository owned by another user")
def test_clone_across_users(tmp_path: Path) -> None:
    """The API (one uid) creates origins; the worker (root) clones them."""
    origin = _origin(tmp_path)
    for root, dirs, files in os.walk(origin):
        for name in [*dirs, *files]:
            os.lchown(os.path.join(root, name), 10001, 10001)
    os.chown(origin, 10001, 10001)
    ws = open_workspace(origin, "main")
    try:
        assert (ws.repo / "app.py").read_text() == "x = 1\n"
    finally:
        ws.cleanup()
        shutil.rmtree(origin, ignore_errors=True)
