"""Release stages: release_readiness → release_gate → deploy_staging → post_deploy_verification."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import Deployment, Release, WorkflowRun
from agentic_sdlc.releases import service as releases
from agentic_sdlc.skills.runtime import SkillRuntime
from agentic_sdlc.workflows.delivery import (
    DeliveryState,
    Escalate,
    _gate,
    agent_ctx,
    escalate_now,
    node,
    stage,
)
from agentic_sdlc.workflows.stages_build import scope_criteria


def _release(db: Session, run: WorkflowRun) -> Release:
    rel = db.get(Release, uuid.UUID(run.outcome["release_id"]))
    assert rel is not None
    return rel


@node("release_readiness")
def release_readiness(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "release_readiness") as (db, run, budget):
        if "release_id" not in run.outcome:
            facts = releases.gather_facts(db, run, scope_criteria(db, run))
            report = (
                SkillRuntime()
                .execute(
                    "release-readiness",
                    {"facts": facts},
                    agent_ctx(db, run, "shipping", "release_readiness", budget),
                )
                .output
            )
            release = releases.create_release(db, run, report)
            run.outcome = {**run.outcome, "release_id": str(release.id)}
        release = _release(db, run)
        if not release.readiness["eligible"]:
            escalate_now(
                db,
                run,
                "release_readiness",
                "release is not eligible: " + "; ".join(release.readiness["blockers"]),
            )
            return {"halt": True}
    return {"outcome": "ok"}


@node("release_gate")
def release_gate(state: DeliveryState) -> DeliveryState:
    def approved(db: Session, run: WorkflowRun) -> None:
        _release(db, run).status = "approved"

    def changes(db: Session, run: WorkflowRun, comment: str) -> str:
        raise Escalate(f"release changes requested: {comment}")

    return _gate(
        state,
        gate="release_staging",
        artifact_type="release",
        artifact_id_key="release_id",
        action="Approve deployment of this release candidate to staging.",
        on_approved=approved,
        on_changes=changes,
    )


@node("deploy_staging")
def deploy_staging(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "deploy_staging") as (db, run, _):
        release = _release(db, run)
        existing = db.scalar(
            select(Deployment).where(Deployment.release_id == release.id, Deployment.environment == "staging")
        )
        if existing is None:
            dep = releases.deploy(db, release, "staging")
            run.outcome = {**run.outcome, "staging_deployment_id": str(dep.id)}
            if dep.status != "succeeded":
                escalate_now(db, run, "deploy_staging", f"staging deployment {dep.status}: {dep.log[-300:]}")
                return {"halt": True}
    return {"outcome": "ok"}


@node("post_deploy_verification")
def post_deploy_verification(state: DeliveryState) -> DeliveryState:
    with stage(state["run_id"], "post_deploy_verification") as (db, run, budget):
        release = _release(db, run)
        dep = db.get(Deployment, uuid.UUID(run.outcome["staging_deployment_id"]))
        assert dep is not None
        if dep.status == "succeeded" and not dep.smoke:
            results = releases.verify_deployment(db, dep, release)
            verdict = (
                SkillRuntime()
                .execute(
                    "deployment-validation",
                    {"results": results},
                    agent_ctx(db, run, "shipping", "post_deploy_verification", budget),
                )
                .output
            )
            dep.health = {**dep.health, "verdict": verdict}
            if verdict["recommendation"] == "rollback":
                releases.rollback(
                    db, dep, "post-deployment checks failed: " + "; ".join(verdict["failed_checks"])
                )
                escalate_now(
                    db,
                    run,
                    "post_deploy_verification",
                    "staging deployment rolled back: " + "; ".join(verdict["failed_checks"]),
                )
                return {"halt": True}
        run.outcome = {
            **run.outcome,
            "production": (
                "eligible for a production promotion request"
                if get_settings().production_deploy_enabled
                else "production deployment is disabled by policy; release stops at staging"
            ),
        }
    return {"outcome": "ok"}
