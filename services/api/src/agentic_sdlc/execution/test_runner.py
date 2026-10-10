"""Run a repository's tests (and optionally an independent acceptance suite) in the sandbox.

The commit is exported with `git archive` (no `.git`), extracted with path checks, given to the
sandbox, and deleted afterwards. Evidence comes from the asw_evidence pytest plugin and is
cross-checked against pytest's exit code. Changes to tracked files during the run are detected by
hashing, not by running git in the sandbox-written tree.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.execution.sandbox import SandboxResult, get_sandbox
from agentic_sdlc.execution.workspace import GIT_SAFE, new_dir

PLUGIN = Path(__file__).with_name("pytest_evidence.py")
EXIT = {0: "passed", 1: "failed", 2: "interrupted", 3: "internal_error", 4: "usage_error", 5: "no_tests"}


@dataclass
class SuiteResult:
    status: str  # passed | failed | error
    totals: dict[str, int]
    cases: list[dict[str, Any]]
    log: str
    command: list[str]
    sandbox: dict[str, Any]
    commit: str
    tampered_files: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def export_tree(origin: Path, commit: str, dest: Path) -> list[str]:
    """Extract `commit` into dest without `.git`. Rejects links and paths escaping dest."""
    archive = subprocess.run(
        ["git", *GIT_SAFE, "archive", "--format=tar", commit],
        cwd=origin,
        capture_output=True,
        check=True,
        timeout=300,
        env={"PATH": "/usr/bin:/bin", "HOME": str(origin), "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout
    names = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not target.is_relative_to(dest.resolve()) or m.issym() or m.islnk() or m.isdev():
                raise ValueError(f"unsafe archive member {m.name}")
            if m.isfile():
                names.append(m.name)
        tar.extractall(dest, filter="data")
    return names


def _hash_files(root: Path, names: list[str]) -> dict[str, str]:
    out = {}
    for n in names:
        p = root / n
        out[n] = (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else "<missing>"
        )
    return out


def run_suite(
    origin: Path,
    commit: str,
    *,
    acceptance_dir: Path | None = None,
    extra_targets: list[str] | None = None,
    timeout_s: int | None = None,
) -> SuiteResult:
    settings = get_settings()
    work = new_dir("tree")
    tree = work / "tree"
    tree.mkdir()
    try:
        tracked = export_tree(origin, commit, tree)
        asw = tree / ".asw"
        asw.mkdir()
        shutil.copy(PLUGIN, asw / "asw_evidence.py")
        targets: list[str] = []
        if acceptance_dir is not None:
            shutil.copytree(
                acceptance_dir, asw / "acceptance", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
            targets.append(".asw/acceptance")
            if (tree / "tests").is_dir():
                targets.append("tests")
        targets += extra_targets or []
        before = _hash_files(tree, tracked)
        command = [
            settings.sandbox_python,
            "-m",
            "pytest",
            "-p",
            "asw_evidence",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            ".",
            *targets,
        ]
        result: SandboxResult = get_sandbox().run(
            command,
            tree,
            timeout_s=timeout_s,
            env={"PYTHONPATH": ".asw:.", "ASW_EVIDENCE_FILE": ".asw/evidence.jsonl"},
        )
        cases = []
        evidence = asw / "evidence.jsonl"
        if evidence.exists() and evidence.stat().st_size < 5_000_000:
            for line in evidence.read_text(errors="replace").splitlines():
                try:
                    c = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(c, dict) and {"nodeid", "outcome"} <= set(c):
                    cases.append(
                        {
                            "nodeid": str(c["nodeid"])[:300],
                            "outcome": str(c["outcome"]),
                            "criteria": [str(k) for k in c.get("criteria", [])][:20],
                            "message": str(c.get("message", ""))[:500],
                            "duration_s": c.get("duration_s"),
                        }
                    )
        after = _hash_files(tree, tracked)
        tampered = sorted(n for n in tracked if before[n] != after[n])
        totals = {
            k: sum(1 for c in cases if c["outcome"] == k) for k in ("passed", "failed", "skipped", "error")
        }
        totals["total"] = len(cases)
        problems = []
        exit_meaning = EXIT.get(result.exit_code, f"exit {result.exit_code}")
        if result.timed_out:
            problems.append("test run timed out")
        if result.exit_code == 0 and (totals["failed"] or totals["error"]):
            problems.append("evidence reports failures but pytest exited 0")
        if result.exit_code == 1 and not (totals["failed"] or totals["error"]):
            problems.append("pytest reported failures that the evidence does not show")
        if result.exit_code not in (0, 1):
            problems.append(f"pytest did not complete normally ({exit_meaning})")
        if tampered:
            problems.append("tests modified tracked files")
        if problems:
            status = "error"
        elif result.exit_code == 0 and totals["total"] > 0:
            status = "passed"
        else:
            status = "failed"
        log = (result.stdout + ("\n--- stderr ---\n" + result.stderr if result.stderr.strip() else ""))[
            -20000:
        ]
        return SuiteResult(
            status=status,
            totals=totals,
            cases=cases,
            log=log,
            command=command,
            sandbox={**result.summary(), "exit_meaning": exit_meaning},
            commit=commit,
            tampered_files=tampered,
            problems=problems,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
