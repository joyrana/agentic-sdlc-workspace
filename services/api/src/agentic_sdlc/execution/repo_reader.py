"""Read-only repository access through git objects.

Reading `git ls-tree` / `git show <rev>:<path>` instead of the working tree means symlinks, hooks
or a tampered checkout cannot redirect reads outside the repository, and nothing is executed.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Any

from agentic_sdlc.core.errors import ValidationFailed
from agentic_sdlc.core.gitenv import git_env

MAX_FILE_BYTES = 200_000


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "safe.directory=*", *args],
        cwd=repo,
        env=git_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise ValidationFailed(f"git {args[0]} failed", details={"stderr": proc.stderr[-500:]})
    return proc.stdout


def safe_relpath(path: str) -> str:
    """Repository-relative path validation shared by reads and patches."""
    p = path.strip().replace("\\", "/")
    if (
        not p
        or p.startswith("/")
        or any(part in ("", ".", "..") for part in p.split("/"))
        or p.startswith(".git")
    ):
        raise ValidationFailed("invalid repository path", details={"path": path[:200]})
    if not re.match(r"^[\w./@+-]+$", p):
        raise ValidationFailed("unsupported characters in path", details={"path": path[:200]})
    return p


def head_commit(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def list_files(repo: Path, rev: str = "HEAD") -> list[dict[str, Any]]:
    out = _git(repo, "ls-tree", "-r", "-l", "--full-tree", rev)
    files = []
    for line in out.splitlines():
        meta, _, path = line.partition("\t")
        mode, typ, _sha, size = meta.split()
        if typ == "blob" and mode != "120000":  # skip symlinks
            files.append({"path": path, "size": int(size) if size.isdigit() else 0})
    return files


def read_file(repo: Path, path: str, rev: str = "HEAD") -> str:
    p = safe_relpath(path)
    size = _git(repo, "cat-file", "-s", f"{rev}:{p}").strip()
    if int(size) > MAX_FILE_BYTES:
        raise ValidationFailed("file too large to read", details={"path": p, "bytes": int(size)})
    return _git(repo, "show", f"{rev}:{p}")


def python_symbols(source: str) -> dict[str, Any]:
    """Static outline of a Python module (never imported or executed)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": f"syntax error line {exc.lineno}"}
    classes, functions, imports = [], [], []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
            fields = [
                n.target.id
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            ]
            decorators = [ast.unparse(d) for d in node.decorator_list]
            classes.append(
                {
                    "name": node.name,
                    "methods": methods,
                    "fields": fields,
                    "decorators": decorators,
                    "bases": [ast.unparse(b) for b in node.bases],
                }
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append({"name": node.name, "args": [a.arg for a in node.args.args]})
        elif isinstance(node, ast.Import | ast.ImportFrom):
            imports.append(ast.unparse(node))
    annotated = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and (n.returns is not None))
    total_fns = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    return {
        "classes": classes,
        "functions": functions,
        "imports": imports,
        "typed_functions": annotated,
        "functions_total": total_fns,
    }
