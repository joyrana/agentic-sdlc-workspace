from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import select, text

from agentic_sdlc import __version__
from agentic_sdlc.api.deps import DB, CurrentPrincipal
from agentic_sdlc.api.schemas import DevUser, MeOut, SystemInfo
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import Membership
from agentic_sdlc.seed import DEV_USERS

router = APIRouter(tags=["system"])

_ROLE_HINTS = {
    "dev_admin": "global admin (no approval authority)",
    "dev_owner": "project owner of projects they create",
    "dev_engineer": "engineer when added to a project",
    "dev_reviewer": "reviewer when added to a project",
    "dev_qa": "qa when added to a project",
    "dev_approver": "release approver when added to a project",
    "dev_auditor": "auditor when added to a project",
}


@router.get("/health")
def health(db: DB) -> dict[str, str]:
    db.execute(text("select 1"))
    return {"status": "ok"}


@router.get("/system/info", response_model=SystemInfo)
def info() -> SystemInfo:
    s = get_settings()
    return SystemInfo(
        version=__version__,
        app_env=s.app_env,
        auth_mode=s.auth_mode,
        dev_login_enabled=s.app_env in ("development", "test") and s.dev_seed_users,
        sandbox_backend=s.sandbox_backend,
        model_inference="configured" if s.model_endpoint_url else "unavailable",
        production_deploy_enabled=s.production_deploy_enabled,
    )


@router.get("/system/dev-users", response_model=list[DevUser])
def dev_users() -> list[DevUser]:
    """Development-only convenience for the sign-in screen. Returns 404 outside dev/test."""
    s = get_settings()
    if s.app_env not in ("development", "test") or not s.dev_seed_users:
        from agentic_sdlc.core.errors import NotFound

        raise NotFound("not available")
    return [
        DevUser(email=e, display_name=n, token=t, roles_hint=_ROLE_HINTS[t]) for e, n, _admin, t in DEV_USERS
    ]


@router.get("/me", response_model=MeOut)
def me(db: DB, principal: CurrentPrincipal) -> MeOut:
    roles: dict[str, list[str]] = defaultdict(list)
    for m in db.scalars(select(Membership).where(Membership.user_id == principal.user_id)):
        roles[str(m.project_id)].append(m.role)
    return MeOut(
        id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        is_admin=principal.is_admin,
        project_roles=dict(roles),
    )
