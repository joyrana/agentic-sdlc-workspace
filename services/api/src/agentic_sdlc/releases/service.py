"""Release readiness, artifacts, deployment and post-deployment verification."""

from __future__ import annotations

import gzip
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.approvals import service as approvals
from agentic_sdlc.audit import service as audit
from agentic_sdlc.core.db import utcnow
from agentic_sdlc.core.errors import PolicyViolation
from agentic_sdlc.core.gitenv import git_env
from agentic_sdlc.core.hashing import content_hash, sha256_bytes
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.core.storage import get_storage
from agentic_sdlc.events import service as events
from agentic_sdlc.execution.sandbox import get_sandbox
from agentic_sdlc.execution.workspace import GIT_SAFE, diff_between, new_dir, review_patch
from agentic_sdlc.orm import (
    AcceptanceCriterion,
    ApprovalRequest,
    CodeChange,
    Defect,
    Deployment,
    ImplementationPlan,
    ProjectRepository,
    Release,
    TestRun,
    VerificationResult,
    WorkflowRun,
)
from agentic_sdlc.releases.adapters import LocalStagingAdapter, adapter_for, manifest_of


def _approved(db: Session, run_id: uuid.UUID, gate: str) -> dict[str, Any]:
    req = approvals.latest_request(db, run_id, gate)
    if req is None or req.status != "approved":
        return {"approved": False, "request_status": req.status if req else None}
    _, digest, _ = approvals.current_artifact(db, req.artifact_type, req.artifact_id)
    return {
        "approved": digest == req.content_hash,
        "request_id": str(req.id),
        "stale": digest != req.content_hash,
    }


def gather_facts(db: Session, run: WorkflowRun, scope: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect every fact the release policy needs, from recorded evidence only."""
    attempt = int(run.counters.get("attempt", 1))
    head = run.outcome.get("head_commit")
    repo = db.get(ProjectRepository, uuid.UUID(run.config["repository_id"]))
    assert repo is not None
    verdicts = {
        c.key: v.status
        for v, c in db.execute(
            select(VerificationResult, AcceptanceCriterion)
            .join(AcceptanceCriterion, AcceptanceCriterion.id == VerificationResult.acceptance_criterion_id)
            .where(VerificationResult.run_id == run.id, VerificationResult.attempt == attempt)
        ).all()
    }
    regression = db.scalar(
        select(TestRun).where(
            TestRun.run_id == run.id, TestRun.suite == "regression", TestRun.attempt == attempt
        )
    )
    changes = db.scalars(select(CodeChange).where(CodeChange.run_id == run.id)).all()
    open_defects = db.scalars(select(Defect).where(Defect.run_id == run.id, Defect.status == "open")).all()
    full_diff, files = (
        diff_between(Path(repo.location), run.outcome["base_commit"], head) if head else ("", [])
    )
    plan = db.get(ImplementationPlan, uuid.UUID(run.outcome["plan_id"]))
    scan = (
        review_patch(full_diff, allowed_files=set(plan.content["files"]) if plan else None)
        if full_diff
        else None
    )
    deploy_descriptor = (
        subprocess.run(
            ["git", *GIT_SAFE, "cat-file", "-e", f"{head}:deploy.yaml"],
            cwd=repo.location,
            capture_output=True,
            check=False,
            env=git_env(),
        ).returncode
        == 0
        if head
        else False
    )
    return {
        "head_commit": head,
        "attempt": attempt,
        "architecture_approval": _approved(db, run.id, "architecture"),
        "scope_approval": _approved(db, run.id, "implementation_scope"),
        "criteria": [
            {"key": c["key"], "priority": c["priority"], "status": verdicts.get(c["key"], "missing")}
            for c in scope
        ],
        "regression": None
        if regression is None
        else {"status": regression.status, "commit": regression.commit, "totals": regression.totals},
        "code_reviews": [
            {"attempt": c.attempt, "ok": bool(c.review.get("ok")), "status": c.status} for c in changes
        ],
        "open_defects": [{"title": d.title, "severity": d.severity} for d in open_defects],
        "change_scan": None if scan is None else scan.as_dict(),
        "changed_files": files,
        "deploy_descriptor": deploy_descriptor,
        "external_ci": "not configured (platform sandbox runs are the CI evidence)",
    }


def evaluate(facts: dict[str, Any]) -> dict[str, Any]:
    """Pure release policy. Blockers make a release ineligible; warnings are shown to the approver."""
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, *, warn_only: bool = False) -> None:
        checks.append(
            {"name": name, "status": "pass" if ok else ("warn" if warn_only else "fail"), "detail": detail}
        )

    check(
        "architecture approved",
        facts["architecture_approval"]["approved"],
        "approved for the current proposal content"
        if facts["architecture_approval"]["approved"]
        else f"approval state: {facts['architecture_approval']}",
    )
    check(
        "implementation scope approved",
        facts["scope_approval"]["approved"],
        "approved for the current plan content"
        if facts["scope_approval"]["approved"]
        else f"approval state: {facts['scope_approval']}",
    )
    musts = [c for c in facts["criteria"] if c["priority"] == "must"]
    others = [c for c in facts["criteria"] if c["priority"] != "must"]
    must_bad = [c for c in musts if c["status"] != "verified"]
    check(
        "must-have criteria verified",
        not must_bad,
        f"{len(musts) - len(must_bad)}/{len(musts)} verified"
        + (
            f"; not verified: {', '.join(c['key'] + '=' + c['status'] for c in must_bad)}" if must_bad else ""
        ),
    )
    failed_other = [c for c in others if c["status"] == "failed"]
    check(
        "no failed criteria",
        not failed_other,
        "none failed" if not failed_other else ", ".join(c["key"] for c in failed_other),
    )
    unverifiable = [c for c in others if c["status"] in ("unverifiable", "missing")]
    check(
        "other criteria verifiable",
        not unverifiable,
        "all verified"
        if not unverifiable
        else f"needs human verification: {', '.join(c['key'] for c in unverifiable)}",
        warn_only=True,
    )
    reg = facts["regression"]
    check(
        "regression suite passed on release commit",
        bool(reg and reg["status"] == "passed" and reg["commit"] == facts["head_commit"]),
        f"{reg['totals'].get('passed', 0)}/{reg['totals'].get('total', 0)} passed"
        if reg
        else "no regression run",
    )
    serious = [d for d in facts["open_defects"] if d["severity"] in ("critical", "major")]
    check(
        "no open critical or major defects",
        not serious,
        "none open" if not serious else "; ".join(d["title"] for d in serious),
    )
    minor = [d for d in facts["open_defects"] if d["severity"] == "minor"]
    check("no open minor defects", not minor, f"{len(minor)} open", warn_only=True)
    reviews_ok = all(r["ok"] for r in facts["code_reviews"] if r["status"] == "applied")
    check(
        "patch policy reviews passed",
        reviews_ok and bool(facts["code_reviews"]),
        f"{len(facts['code_reviews'])} change set(s) reviewed",
    )
    scan = facts["change_scan"]
    check(
        "full change scanned for credentials and scope",
        bool(scan and scan["ok"]),
        "clean" if scan and scan["ok"] else f"violations: {scan['violations'] if scan else 'no change'}",
    )
    if scan and scan["warnings"]:
        check("risky constructs", False, "; ".join(scan["warnings"]), warn_only=True)
    check(
        "deployment descriptor present",
        facts["deploy_descriptor"],
        "deploy.yaml found at release commit"
        if facts["deploy_descriptor"]
        else "no deploy.yaml; staging smoke tests cannot run",
    )
    # Shown as a warning, not a pass: an unconfigured external CI is a gap the approver should see.
    check(
        "external CI",
        not str(facts["external_ci"]).startswith("not configured"),
        facts["external_ci"],
        warn_only=True,
    )
    blockers = [c for c in checks if c["status"] == "fail"]
    warnings = [c for c in checks if c["status"] == "warn"]
    return {
        "eligible": not blockers,
        "blockers": [c["name"] for c in blockers],
        "warnings": [f"{c['name']}: {c['detail']}" for c in warnings],
        "checks": checks,
        "facts": facts,
    }


def build_artifact(origin: Path, commit: str) -> tuple[str, str, int]:
    """Deterministic tar.gz of the commit tree (git archive with fixed mtime). Returns (key, sha256, size)."""
    tar = subprocess.run(
        ["git", *GIT_SAFE, "archive", "--format=tar", commit],
        cwd=origin,
        capture_output=True,
        check=True,
        timeout=300,
        env=git_env(),
    ).stdout
    data = gzip.compress(tar, mtime=0)
    key = get_storage().put(data)
    return key, sha256_bytes(data), len(data)


def create_release(db: Session, run: WorkflowRun, report: dict[str, Any]) -> Release:
    existing = db.scalar(select(Release).where(Release.run_id == run.id))
    if existing is not None:
        return existing
    repo = db.get(ProjectRepository, uuid.UUID(run.config["repository_id"]))
    assert repo is not None
    key, sha, size = build_artifact(Path(repo.location), run.outcome["head_commit"])
    release = Release(
        project_id=run.project_id,
        run_id=run.id,
        version_label=f"rc-{utcnow().strftime('%Y%m%d')}-{str(run.id)[:8]}",
        commit=run.outcome["head_commit"],
        artifact_sha256=sha,
        artifact_key=key,
        readiness={**report, "artifact": {"sha256": sha, "bytes": size, "format": "tar.gz (git archive)"}},
        readiness_hash=content_hash(report),
        status="candidate" if report["eligible"] else "blocked",
    )
    db.add(release)
    db.flush()
    audit.record(
        db,
        action="release.created",
        actor_id=None,
        actor_type="agent",
        project_id=run.project_id,
        run_id=run.id,
        target_type="release",
        target_id=release.id,
        details={
            "version": release.version_label,
            "commit": release.commit,
            "artifact_sha256": sha,
            "eligible": report["eligible"],
            "blockers": report["blockers"],
        },
    )
    events.emit(
        db,
        type="release.candidate",
        project_id=run.project_id,
        run_id=run.id,
        payload={"version": release.version_label, "eligible": report["eligible"]},
        artifact_refs=[{"type": "release", "id": str(release.id)}],
    )
    return release


def deploy(db: Session, release: Release, environment: str) -> Deployment:
    """Deploy an approved release. Re-verifies the approval for this exact artifact and readiness."""
    gate = "release_staging" if environment == "staging" else "release_production"
    req = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.run_id == release.run_id,
            ApprovalRequest.gate == gate,
            ApprovalRequest.artifact_id == release.id,
            ApprovalRequest.status == "approved",
        )
    )
    if req is None:
        raise PolicyViolation(f"no approved {gate} decision for this release")
    _, digest, _ = approvals.current_artifact(db, "release", release.id)
    if digest != req.content_hash:
        raise PolicyViolation("the release changed after it was approved")
    adapter = adapter_for(environment)
    dep = Deployment(
        release_id=release.id,
        project_id=release.project_id,
        environment=environment,
        adapter=adapter.name,
        approval_request_id=req.id,
        status="pending",
        started_at=utcnow(),
    )
    db.add(dep)
    db.flush()
    try:
        result = adapter.deploy(
            project_id=release.project_id,
            release_id=release.id,
            artifact_key=release.artifact_key,
            artifact_sha256=release.artifact_sha256,
        )
    except PolicyViolation as exc:
        dep.status, dep.log, dep.finished_at = "blocked", str(exc), utcnow()
        raise
    dep.status = result.status
    dep.log = "\n".join(result.log)
    dep.rollback = {"previous": result.previous}
    dep.finished_at = utcnow()
    release.status = f"deployed_{environment}" if result.status == "succeeded" else "failed"
    audit.record(
        db,
        action="release.deployed",
        actor_id=None,
        actor_type="agent",
        project_id=release.project_id,
        run_id=release.run_id,
        target_type="deployment",
        target_id=dep.id,
        details={
            "environment": environment,
            "adapter": adapter.name,
            "status": dep.status,
            "artifact_sha256": release.artifact_sha256,
            "approval_request_id": str(req.id),
        },
    )
    events.emit(
        db,
        type="deployment.completed",
        project_id=release.project_id,
        run_id=release.run_id,
        payload={"environment": environment, "status": dep.status, "adapter": adapter.name},
        artifact_refs=[{"type": "deployment", "id": str(dep.id)}],
    )
    return dep


def _commands(tree: Path, section: str) -> list[dict[str, Any]]:
    descriptor = (
        yaml.safe_load((tree / "deploy.yaml").read_text()) if (tree / "deploy.yaml").is_file() else {}
    )
    out: list[dict[str, Any]] = []
    for entry in (descriptor or {}).get(section, []) or []:
        cmd = entry.get("command") if isinstance(entry, dict) else None
        if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
            continue
        if cmd[0] not in ("python", "python3"):
            out.append({"name": str(entry.get("name", "?")), "rejected": "only python commands are allowed"})
            continue
        out.append(
            {"name": str(entry.get("name", "check")), "command": [get_settings().sandbox_python, *cmd[1:]]}
        )
    return out


def verify_deployment(db: Session, dep: Deployment, release: Release) -> dict[str, Any]:
    """Integrity + smoke + health against the deployed tree, executed in the sandbox."""
    adapter = LocalStagingAdapter()
    deployed = adapter.current_tree(release.project_id)
    results: dict[str, Any] = {"integrity": None, "smoke": [], "health": []}
    if deployed is None:
        results["integrity"] = {"ok": False, "detail": "nothing deployed"}
        return results
    work = new_dir("deployed")
    tree = work / "tree"
    try:
        shutil.copytree(deployed, tree, symlinks=False)
        data = get_storage().get(release.artifact_key)
        expected = Path(work / "expected")
        expected.mkdir()
        from agentic_sdlc.releases.adapters import _safe_extract

        _safe_extract(data, expected)
        ok = manifest_of(expected) == manifest_of(tree)
        shutil.rmtree(expected)
        results["integrity"] = {
            "ok": ok,
            "detail": "deployed files match the approved artifact"
            if ok
            else "deployed files differ from the approved artifact",
        }
        sandbox = get_sandbox()
        for section in ("smoke", "health"):
            for cmd in _commands(tree, section):
                if "rejected" in cmd:
                    results[section].append({"name": cmd["name"], "ok": False, "detail": cmd["rejected"]})
                    continue
                r = sandbox.run(cmd["command"], tree, timeout_s=60, env={"PYTHONPATH": "."})
                results[section].append(
                    {
                        "name": cmd["name"],
                        "ok": r.exit_code == 0 and not r.timed_out,
                        "exit_code": r.exit_code,
                        "output": (r.stdout + r.stderr)[-1000:],
                        "duration_s": r.duration_s,
                    }
                )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    dep.smoke = {"checks": results["smoke"], "integrity": results["integrity"]}
    dep.health = {"checks": results["health"]}
    return results


def rollback(db: Session, dep: Deployment, reason: str) -> Deployment:
    adapter = LocalStagingAdapter()
    result = adapter.rollback(project_id=dep.project_id, to_release=(dep.rollback or {}).get("previous"))
    dep.status = "rolled_back"
    dep.rollback = {
        **(dep.rollback or {}),
        "performed": True,
        "reason": reason,
        "log": result.log,
        "at": utcnow().isoformat(),
    }
    release = db.get(Release, dep.release_id)
    if release is not None:
        release.status = "rolled_back"
    audit.record(
        db,
        action="release.rolled_back",
        actor_id=None,
        actor_type="system",
        project_id=dep.project_id,
        target_type="deployment",
        target_id=dep.id,
        details={"reason": reason, "log": result.log},
    )
    events.emit(
        db,
        type="deployment.rolled_back",
        project_id=dep.project_id,
        run_id=release.run_id if release else None,
        payload={"reason": reason},
    )
    return dep
