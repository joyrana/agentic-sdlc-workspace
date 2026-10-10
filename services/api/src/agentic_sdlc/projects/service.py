"""Projects, memberships and repositories."""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_sdlc.audit import service as audit
from agentic_sdlc.auth.service import Principal
from agentic_sdlc.core.errors import Conflict, NotFound, ValidationFailed
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.events import service as events
from agentic_sdlc.orm import Membership, Project, ProjectRepository, User
from agentic_sdlc.orm.identity import ROLES

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def _derive_key(db: Session, name: str) -> str:
    letters = re.sub(r"[^A-Za-z0-9 ]", "", name).upper().split()
    base = ("".join(w[0] for w in letters) if len(letters) > 1 else (letters[0] if letters else "PRJ"))[:6]
    if not base or not base[0].isalpha():
        base = "P" + base
    base = base[:6] if len(base) >= 2 else (base + "X")
    key, n = base, 1
    while db.scalar(select(func.count()).select_from(Project).where(Project.key == key)):
        n += 1
        key = f"{base}{n}"
    return key


def create_project(
    db: Session,
    principal: Principal,
    *,
    name: str,
    key: str | None = None,
    description: str = "",
    goals: str = "",
    constraints: dict[str, Any] | None = None,
    is_sample: bool = False,
) -> Project:
    if not name.strip():
        raise ValidationFailed("name is required")
    if key is not None:
        key = key.upper()
        if not _KEY_RE.match(key):
            raise ValidationFailed("key must be 2-10 chars, start with a letter, A-Z/0-9 only")
    project = Project(
        key=key or _derive_key(db, name),
        name=name.strip(),
        description=description,
        goals=goals,
        constraints=constraints or {},
        is_sample=is_sample,
        created_by=principal.user_id,
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("project key already exists") from exc
    db.add(Membership(project_id=project.id, user_id=principal.user_id, role="project_owner"))
    principal.invalidate_roles()
    audit.record(
        db,
        action="project.created",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        details={"key": project.key, "name": project.name},
    )
    events.emit(db, type="project.created", project_id=project.id, payload={"key": project.key})
    return project


def list_projects(db: Session, principal: Principal) -> list[Project]:
    if principal.is_admin:
        return list(db.scalars(select(Project).order_by(Project.created_at.desc())))
    stmt = (
        select(Project)
        .join(Membership, Membership.project_id == Project.id)
        .where(Membership.user_id == principal.user_id)
        .distinct()
        .order_by(Project.created_at.desc())
    )
    return list(db.scalars(stmt))


def update_project(
    db: Session, principal: Principal, project: Project, *, expected_version: int, changes: dict[str, Any]
) -> Project:
    if project.version != expected_version:
        raise Conflict("project was modified by someone else", details={"current_version": project.version})
    allowed = {"name", "description", "goals", "constraints", "environments", "limits"}
    for field, value in changes.items():
        if field in allowed and value is not None:
            setattr(project, field, value)
    audit.record(
        db,
        action="project.updated",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        details={"fields": sorted(k for k in changes if k in allowed)},
    )
    return project


def add_member(db: Session, principal: Principal, project: Project, *, email: str, role: str) -> Membership:
    if role not in ROLES:
        raise ValidationFailed(f"unknown role '{role}'", details={"allowed": list(ROLES)})
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        raise NotFound("user not found")
    if user.is_service_account:
        raise ValidationFailed("service accounts cannot hold project roles")
    existing = db.scalar(
        select(Membership).where(
            Membership.project_id == project.id, Membership.user_id == user.id, Membership.role == role
        )
    )
    if existing:
        return existing
    m = Membership(project_id=project.id, user_id=user.id, role=role)
    db.add(m)
    audit.record(
        db,
        action="membership.granted",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="user",
        target_id=user.id,
        details={"role": role},
    )
    return m


def remove_member(
    db: Session, principal: Principal, project: Project, *, user_id: uuid.UUID, role: str
) -> None:
    m = db.scalar(
        select(Membership).where(
            Membership.project_id == project.id, Membership.user_id == user_id, Membership.role == role
        )
    )
    if m is None:
        raise NotFound("membership not found")
    if role == "project_owner":
        owners = db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.project_id == project.id, Membership.role == "project_owner")
        )
        if (owners or 0) <= 1:
            raise Conflict("a project must keep at least one owner")
    db.delete(m)
    audit.record(
        db,
        action="membership.revoked",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="user",
        target_id=user_id,
        details={"role": role},
    )


def list_members(db: Session, project_id: uuid.UUID) -> list[tuple[Membership, User]]:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.project_id == project_id)
    ).all()
    return [(m, u) for m, u in rows]


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Agentic SDLC Workspace",
    "GIT_AUTHOR_EMAIL": "platform@agentic-sdlc.invalid",
    "GIT_COMMITTER_NAME": "Agentic SDLC Workspace",
    "GIT_COMMITTER_EMAIL": "platform@agentic-sdlc.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin",
}


def git(args: list[str], cwd: Path, *, home: Path | None = None, timeout: int = 120) -> str:
    env = {**GIT_ENV, "HOME": str(home or cwd)}
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=timeout
    )
    return proc.stdout.strip()


def _materialize_fixture(source: Path, repo_id: uuid.UUID) -> Path:
    """Copy a shipped fixture directory into the data dir and make it a git repository.

    The copy acts as the "remote" for that project; agents only ever work on disposable clones of it.
    """
    import shutil

    dest = get_settings().data_dir / "repositories" / str(repo_id)
    if (dest / ".git").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
    git(["init", "-q", "-b", "main"], dest)
    git(["add", "-A"], dest)
    git(["commit", "-q", "-m", "Initial fixture snapshot"], dest)
    return dest


def add_repository(
    db: Session,
    principal: Principal,
    project: Project,
    *,
    name: str,
    kind: str,
    location: str,
    default_branch: str = "main",
) -> ProjectRepository:
    repo_id = uuid.uuid4()
    if kind == "fixture":
        fixtures = (get_settings().fixtures_dir / "repositories").resolve()
        path = (fixtures / location).resolve()
        if not path.is_relative_to(fixtures) or not path.is_dir():
            raise ValidationFailed("fixture repository not found", details={"location": location})
        stored = str(_materialize_fixture(path, repo_id))
        is_sample = True
        fixture: str | None = path.name
    elif kind == "git":
        if not re.match(r"^https://[\w.-]+/[\w./-]+$", location):
            raise ValidationFailed("only https git URLs are accepted")
        stored, is_sample, fixture = location, False, None
    else:
        raise ValidationFailed("kind must be 'fixture' or 'git'")
    repo = ProjectRepository(
        id=repo_id,
        project_id=project.id,
        name=name,
        kind=kind,
        location=stored,
        default_branch=default_branch,
        is_sample=is_sample,
        fixture=fixture,
    )
    db.add(repo)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("repository name already used in this project") from exc
    audit.record(
        db,
        action="repository.registered",
        actor_id=principal.user_id,
        actor_type=principal.actor_type,
        project_id=project.id,
        target_type="repository",
        target_id=repo.id,
        details={"name": name, "kind": kind},
    )
    return repo
