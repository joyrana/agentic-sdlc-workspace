"""Idempotent seeding: agent service accounts (always) and development users (dev/test only)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.auth.service import hash_token, issue_token
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import ApiToken, User

AGENTS = {
    "business-analyst": "Business Analyst Agent",
    "planner": "Planner Agent",
    "architect": "Architect Agent",
    "developer": "Developer Agent",
    "ba-verifier": "BA Verification Agent",
    "qa": "QA Agent",
    "shipping": "Shipping Agent",
}

# Development users. Tokens are documented in README and refused in production.
DEV_USERS = [
    # (email, display name, is_admin, dev token)
    ("admin@example.dev", "Ada Admin", True, "dev_admin"),
    ("owner@example.dev", "Olu Owner", False, "dev_owner"),
    ("engineer@example.dev", "Eni Engineer", False, "dev_engineer"),
    ("reviewer@example.dev", "Rae Reviewer", False, "dev_reviewer"),
    ("qa@example.dev", "Quinn QA", False, "dev_qa"),
    ("approver@example.dev", "Ari Approver", False, "dev_approver"),
    ("auditor@example.dev", "Aud Auditor", False, "dev_auditor"),
]


def agent_email(agent: str) -> str:
    return f"agent-{agent}@agents.agentic-sdlc.invalid"


def seed_agents(db: Session) -> dict[str, User]:
    out: dict[str, User] = {}
    for key, name in AGENTS.items():
        email = agent_email(key)
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, display_name=name, is_service_account=True)
            db.add(user)
            db.flush()
        out[key] = user
    return out


def seed_dev_users(db: Session) -> list[tuple[User, str]]:
    settings = get_settings()
    if settings.app_env == "production" or not settings.dev_seed_users:
        return []
    created: list[tuple[User, str]] = []
    for email, name, is_admin, token in DEV_USERS:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, display_name=name, is_admin=is_admin)
            db.add(user)
            db.flush()
        if db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token))) is None:
            issue_token(db, user, name="development", ttl_days=None, raw=token)
        created.append((user, token))
    return created


def seed_all(db: Session) -> None:
    seed_agents(db)
    seed_dev_users(db)
