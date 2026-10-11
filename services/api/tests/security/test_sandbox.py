"""Sandbox isolation (threat model T7/T8). These run real processes in real namespaces."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.execution.sandbox import NamespaceSandbox, SandboxError

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(os.geteuid() != 0 or not shutil.which("unshare"), reason="needs root and unshare"),
    pytest.mark.skipif(not Path(get_settings().sandbox_python).exists(), reason="sandbox toolchain missing"),
]

PY = get_settings().sandbox_python


@pytest.fixture
def ws() -> Iterator[Path]:
    root = get_settings().workspaces_dir
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"sbx-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


def py(sb: NamespaceSandbox, ws: Path, code: str, **kw):  # type: ignore[no-untyped-def]
    return sb.run([PY, "-c", code], ws, **kw)


def test_runs_as_unprivileged_user_with_isolated_pids(ws: Path) -> None:
    r = py(
        NamespaceSandbox(),
        ws,
        "import os; print(os.getuid(), os.getgid(), os.getgroups()); "
        "print(len([p for p in os.listdir('/proc') if p.isdigit()]))",
    )
    uid_line, procs = r.stdout.strip().splitlines()
    assert uid_line == f"{get_settings().sandbox_uid} {get_settings().sandbox_uid} []"
    assert int(procs) <= 3  # only the sandboxed tree is visible


def test_network_is_unreachable(ws: Path) -> None:
    r = py(
        NamespaceSandbox(),
        ws,
        "import socket\ntry:\n socket.create_connection(('1.1.1.1', 53), timeout=2); print('OPEN')\n"
        "except OSError as e:\n print('BLOCKED', e.errno)",
    )
    assert "BLOCKED" in r.stdout and "OPEN" not in r.stdout


def test_platform_secrets_and_home_are_masked(ws: Path) -> None:
    data_dir = get_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = data_dir / "secret.txt"
    secret.write_text("PLATFORM-SECRET")
    secret.chmod(0o644)  # even world-readable files are hidden by the mask
    r = py(
        NamespaceSandbox(),
        ws,
        f"import os\nfor p in [{str(secret)!r}, '/root', '/home']:\n"
        " try:\n  print(p, open(p).read() if os.path.isfile(p) else os.listdir(p))\n"
        " except OSError as e:\n  print(p, 'DENIED', type(e).__name__)",
    )
    assert "PLATFORM-SECRET" not in r.stdout
    assert r.stdout.count("DENIED") == 3, r.stdout


def test_only_workspace_is_writable(ws: Path) -> None:
    r = py(
        NamespaceSandbox(),
        ws,
        "import os\nopen('ok.txt','w').write('x'); print('WS-OK')\n"
        "for p in ['/etc/x', '/usr/x', '/opt/asw-sandbox/x']:\n"
        " try:\n  open(p,'w'); print('WROTE', p)\n except OSError:\n  print('DENIED', p)",
    )
    assert "WS-OK" in r.stdout and "WROTE" not in r.stdout
    assert (ws / "ok.txt").read_text() == "x"


def test_shared_tmp_is_private_when_workspace_elsewhere(tmp_path: Path) -> None:
    ws_root = Path("/var/tmp/asw-sbx-test") / uuid.uuid4().hex[:8]
    if str(get_settings().workspaces_dir).startswith("/tmp"):
        ws_root.mkdir(parents=True)
        marker = Path("/tmp") / f"host-marker-{uuid.uuid4().hex[:6]}"
        marker.write_text("host")
        marker.chmod(0o644)
        try:
            r = py(NamespaceSandbox(), ws_root, f"import os; print(os.path.exists({str(marker)!r}))")
            assert r.stdout.strip() == "False"
        finally:
            marker.unlink()
            shutil.rmtree(ws_root.parent, ignore_errors=True)


def test_environment_is_scrubbed(ws: Path) -> None:
    os.environ["ASW_TEST_SECRET"] = "should-not-leak"
    try:
        r = py(NamespaceSandbox(), ws, "import os; print(sorted(os.environ))")
    finally:
        del os.environ["ASW_TEST_SECRET"]
    assert "ASW_TEST_SECRET" not in r.stdout and "DATABASE_URL" not in r.stdout
    with pytest.raises(SandboxError):
        NamespaceSandbox().run(["true"], ws, env={"LD_PRELOAD": "/x.so"})


def test_no_privilege_escalation(ws: Path) -> None:
    r = NamespaceSandbox().run(
        [
            "sh",
            "-c",
            "grep NoNewPrivs /proc/self/status; grep CapEff /proc/self/status; "
            "su -c id root 2>&1 || echo SU-FAILED",
        ],
        ws,
    )
    assert "NoNewPrivs:\t1" in r.stdout
    assert "CapEff:\t0000000000000000" in r.stdout
    assert "SU-FAILED" in r.stdout


def test_timeout_kills_process_tree(ws: Path) -> None:
    r = NamespaceSandbox().run(["sh", "-c", "sleep 60 & sleep 60"], ws, timeout_s=2)
    assert r.timed_out and r.duration_s < 10


def test_process_limit_stops_fork_bomb(ws: Path) -> None:
    r = py(
        NamespaceSandbox(),
        ws,
        "import os\nn=0\ntry:\n for _ in range(1000):\n  if os.fork()==0:\n"
        "   import time; time.sleep(5); os._exit(0)\n"
        "  n+=1\nexcept OSError:\n print('LIMITED', n)",
        timeout_s=20,
    )
    assert "LIMITED" in r.stdout
    assert int(r.stdout.split()[-1]) < get_settings().sandbox_max_processes + 5


def test_memory_and_file_size_limits(ws: Path) -> None:
    mem = py(NamespaceSandbox(), ws, "x = bytearray(4 * 1024**3); print('ALLOCATED')")
    assert "ALLOCATED" not in mem.stdout and mem.exit_code != 0
    big = py(NamespaceSandbox(), ws, "open('big','wb').write(b'0' * (200*1024*1024)); print('WROTE')")
    assert "WROTE" not in big.stdout and big.exit_code != 0


def test_output_is_truncated_and_redacted(ws: Path) -> None:
    # Truncation keeps the tail, so the secret is printed last to exercise redaction.
    r = py(NamespaceSandbox(), ws, "print('y'*200000); print('token=ghp_' + 'a'*30)")
    assert "ghp_aaaa" not in r.stdout and "[REDACTED]" in r.stdout
    assert len(r.stdout) < 70_000 and "truncated" in r.stdout


def test_refuses_to_run_with_a_writable_toolchain(ws: Path, tmp_path: Path) -> None:
    from agentic_sdlc.execution.sandbox import toolchain_problems

    s = get_settings().model_copy()
    fake = tmp_path / "tc" / "bin"
    fake.mkdir(parents=True)
    (fake / "python").symlink_to(get_settings().sandbox_python)
    os.chmod(fake.parent, 0o777)  # noqa: S103 - the unsafe toolchain under test
    s.sandbox_python = str(fake / "python")
    assert any("writable by the sandbox user" in p for p in toolchain_problems(s))
    with pytest.raises(SandboxError, match="toolchain is not protected"):
        NamespaceSandbox(s).run(["true"], ws)
    assert toolchain_problems(get_settings()) == []
