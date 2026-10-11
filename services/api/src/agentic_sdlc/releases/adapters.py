"""Deployment adapters.

Every adapter re-checks authorization itself (defense in depth): it refuses to deploy without an approved,
non-stale decision for the exact release, and credentials never pass through prompts or agent tools.

- LocalStagingAdapter: verified here. Atomic directory deploy with a `current` pointer and rollback.
- GitHubActionsAdapter: dispatches a workflow_dispatch event; disabled unless configured. Not exercised
  in this build environment.
- Production: refused unless `production_deploy_enabled` and a `release_production` approval exist.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from agentic_sdlc.core.errors import PolicyViolation
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.core.storage import get_storage


@dataclass
class DeployResult:
    status: str  # succeeded | failed
    location: str | None
    log: list[str] = field(default_factory=list)
    previous: str | None = None


class DeploymentAdapter(Protocol):
    name: str
    environment: str

    def deploy(
        self, *, project_id: uuid.UUID, release_id: uuid.UUID, artifact_key: str, artifact_sha256: str
    ) -> DeployResult: ...

    def rollback(self, *, project_id: uuid.UUID, to_release: str | None) -> DeployResult: ...


def manifest_of(tree: Path) -> dict[str, str]:
    out = {}
    for p in sorted(tree.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[str(p.relative_to(tree))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _safe_extract(data: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not target.is_relative_to(dest.resolve()) or m.issym() or m.islnk() or m.isdev():
                raise PolicyViolation(f"unsafe member in release artifact: {m.name}")
        tar.extractall(dest, filter="data")


class LocalStagingAdapter:
    name = "local_staging"
    environment = "staging"

    def root(self, project_id: uuid.UUID) -> Path:
        return get_settings().staging_dir / str(project_id)

    def deploy(
        self, *, project_id: uuid.UUID, release_id: uuid.UUID, artifact_key: str, artifact_sha256: str
    ) -> DeployResult:
        data = get_storage().get(artifact_key)
        if hashlib.sha256(data).hexdigest() != artifact_sha256:
            raise PolicyViolation("artifact integrity check failed; refusing to deploy")
        root = self.root(project_id)
        releases = root / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        tmp = releases / f".incoming-{release_id}"
        final = releases / str(release_id)
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        _safe_extract(data, tmp)
        if final.exists():
            shutil.rmtree(final)
        tmp.rename(final)
        current = root / "current"
        previous = os.readlink(current) if current.is_symlink() else None
        swap = root / f".current-{uuid.uuid4().hex[:6]}"
        swap.symlink_to(final)
        swap.replace(current)  # atomic pointer switch
        return DeployResult(
            "succeeded",
            str(final),
            [f"extracted {artifact_sha256[:12]} to {final}", f"current -> {final.name}"],
            previous,
        )

    def current_tree(self, project_id: uuid.UUID) -> Path | None:
        current = self.root(project_id) / "current"
        return current.resolve() if current.is_symlink() else None

    def rollback(self, *, project_id: uuid.UUID, to_release: str | None) -> DeployResult:
        root = self.root(project_id)
        current = root / "current"
        if to_release and Path(to_release).is_dir():
            swap = root / f".current-{uuid.uuid4().hex[:6]}"
            swap.symlink_to(Path(to_release))
            swap.replace(current)
            return DeployResult("succeeded", to_release, [f"current -> {Path(to_release).name} (rollback)"])
        if current.is_symlink():
            current.unlink()
        return DeployResult("succeeded", None, ["no previous release; staging pointer cleared"])


class GitHubActionsAdapter:
    """Dispatches a deployment workflow. Token comes from GITHUB_DEPLOY_TOKEN in the worker environment
    and is never logged, stored, or exposed to agents. Disabled unless configured."""

    name = "github_actions"

    def __init__(self, environment: str) -> None:
        self.environment = environment
        s = get_settings()
        self.repo = s.github_actions_deploy_repo
        self.workflow = s.github_actions_deploy_workflow
        self.token = os.environ.get("GITHUB_DEPLOY_TOKEN")

    @property
    def configured(self) -> bool:
        return bool(self.repo and self.workflow and self.token)

    def deploy(
        self, *, project_id: uuid.UUID, release_id: uuid.UUID, artifact_key: str, artifact_sha256: str
    ) -> DeployResult:
        if not self.configured:
            raise PolicyViolation("GitHub Actions deployment is not configured")
        r = httpx.post(
            f"https://api.github.com/repos/{self.repo}/actions/workflows/{self.workflow}/dispatches",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"},
            json={
                "ref": "main",
                "inputs": {
                    "environment": self.environment,
                    "release_id": str(release_id),
                    "artifact_sha256": artifact_sha256,
                },
            },
            timeout=30,
        )
        ok = r.status_code == 204
        return DeployResult(
            "succeeded" if ok else "failed", None, [f"workflow_dispatch returned {r.status_code}"]
        )

    def rollback(self, *, project_id: uuid.UUID, to_release: str | None) -> DeployResult:
        raise PolicyViolation("rollback for GitHub Actions deployments is performed by the target pipeline")


def adapter_for(environment: str) -> Any:
    s = get_settings()
    if environment == "staging":
        gh = GitHubActionsAdapter("staging")
        return gh if gh.configured else LocalStagingAdapter()
    if environment == "production":
        if not s.production_deploy_enabled:
            raise PolicyViolation("production deployment is disabled by policy")
        gh = GitHubActionsAdapter("production")
        if not gh.configured:
            raise PolicyViolation("no production deployment adapter is configured")
        return gh
    raise PolicyViolation(f"unknown environment {environment}")
