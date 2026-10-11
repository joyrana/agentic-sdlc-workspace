from __future__ import annotations

from agentic_sdlc.execution.workspace import review_patch
from tests.helpers import REPO

GOOD = (REPO / "samples" / "coding" / "sample-task-service" / "attempt-1.patch").read_text()
ALLOWED = {"task_service/service.py", "task_service/errors.py"}


def _diff(path: str, body: str = "+x = 1\n") -> str:
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1 @@\n{body}"
    )


def test_sample_patch_passes_policy() -> None:
    r = review_patch(GOOD, allowed_files=ALLOWED)
    assert r.ok, r.violations
    assert "tests/test_title_validation.py" in r.files and r.added_lines > 10


def test_paths_outside_repository_rejected() -> None:
    for bad in ("../etc/passwd", "/abs/file.py", ".git/hooks/pre-commit", "a/../../b.py"):
        assert not review_patch(_diff(bad)).ok, bad


def test_files_outside_plan_rejected_but_new_tests_allowed() -> None:
    assert not review_patch(_diff("task_service/admin.py"), allowed_files=ALLOWED).ok
    assert review_patch(_diff("tests/test_new.py"), allowed_files=ALLOWED).ok


def test_binary_symlink_and_credentials_rejected() -> None:
    binary = "diff --git a/x.bin b/x.bin\nGIT binary patch\nliteral 1\n"
    assert "binary patches are not accepted" in review_patch(binary).violations
    link = "diff --git a/l b/l\nnew file mode 120000\n--- /dev/null\n+++ b/l\n@@ -0,0 +1 @@\n+/etc/passwd\n"
    assert any("symlinks" in v for v in review_patch(link).violations)
    secret = _diff("cfg.py", "+API_KEY = 'sk-" + "a" * 30 + "'\n")
    assert "added line looks like a credential" in review_patch(secret).violations


def test_risky_code_is_flagged_not_blocked() -> None:
    r = review_patch(_diff("task_service/x.py", "+import subprocess\n+subprocess.run(['ls'])\n"))
    assert r.ok and r.warnings == ["added code starts processes"]


def test_empty_and_oversized_patches_rejected() -> None:
    assert not review_patch("   ").ok
    assert not review_patch(_diff("a.py", "+" + "x" * 1_000_001 + "\n")).ok
