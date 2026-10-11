"""Authentication (token → principal) and authorization (principal, project, permission)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.auth.policy import permissions_for
from agentic_sdlc.core.db import utcnow
from agentic_sdlc.core.errors import Forbidden, NotFound, Unauthorized
from agentic_sdlc.orm import ApiToken, Membership, Project, User

TOKEN_PREFIX = "asw_"  # noqa: S105 - a prefix, not a secret


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(db: Session, user: User, *, name: str, ttl_days: int | None, raw: str | None = None) -> str:
    """Create a token. `raw` lets development seeding use documented values; never used in production."""
    token = raw or TOKEN_PREFIX + secrets.token_urlsafe(32)
    db.add(
        ApiToken(
            user_id=user.id,
            name=name,
            token_hash=hash_token(token),
            expires_at=(utcnow() + timedelta(days=ttl_days)) if ttl_days else None,
        )
    )
    return token


@dataclass
class Principal:
    user_id: uuid.UUID
    email: str
    display_name: str
    is_admin: bool
    is_service_account: bool
    token_id: uuid.UUID | None = None
    _roles: dict[uuid.UUID, set[str]] = field(default_factory=dict, repr=False)

    @property
    def actor_type(self) -> str:
        return "agent" if self.is_service_account else "human"

    def roles_in(self, db: Session, project_id: uuid.UUID) -> set[str]:
        if project_id not in self._roles:
            rows = db.scalars(
                select(Membership.role).where(
                    Membership.project_id == project_id, Membership.user_id == self.user_id
                )
            ).all()
            self._roles[project_id] = set(rows)
        return self._roles[project_id]

    def invalidate_roles(self) -> None:
        self._roles.clear()


def principal_from_user(user: User, token_id: uuid.UUID | None = None) -> Principal:
    return Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        is_service_account=user.is_service_account,
        token_id=token_id,
    )


def authenticate(db: Session, authorization: str | None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    raw = authorization.split(" ", 1)[1].strip()
    row = db.execute(
        select(ApiToken, User)
        .join(User, User.id == ApiToken.user_id)
        .where(ApiToken.token_hash == hash_token(raw))
    ).first()
    if row is None:
        raise Unauthorized("invalid token")
    token, user = row
    if token.revoked_at is not None or (token.expires_at is not None and token.expires_at < utcnow()):
        raise Unauthorized("token expired or revoked")
    if not user.is_active:
        raise Unauthorized("user disabled")
    if user.is_service_account:
        # Agents never call the public API; they act through the worker with internal principals.
        raise Unauthorized("service accounts cannot use the public API")
    return principal_from_user(user, token.id)


def authorize(db: Session, principal: Principal, project_id: uuid.UUID, permission: str) -> Project:
    """Return the project if `principal` holds `permission` in it.

    Non-members get 404 (no existence leak); members lacking the permission get 403.
    """
    project = db.get(Project, project_id)
    roles = principal.roles_in(db, project_id) if project else set()
    if project is None or (not roles and not principal.is_admin):
        raise NotFound("project not found")
    if permission not in permissions_for(roles, is_admin=principal.is_admin):
        raise Forbidden(f"missing permission '{permission}'", details={"permission": permission})
    return project
