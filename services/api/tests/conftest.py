"""Shared fixtures. Tests run against a real PostgreSQL database (default: sdlc_test).

The schema is dropped and re-migrated once per session. Audit events are immutable (no TRUNCATE),
so tests isolate themselves by creating their own projects rather than cleaning tables.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="asw-test-"))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://sdlc:sdlc@localhost:5432/sdlc_test")
os.environ["APP_ENV"] = "test"
os.environ["DATA_DIR"] = str(_TMP / "var")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from agentic_sdlc.core.db import get_engine, session_scope  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]

TOKENS = {
    "admin": "dev_admin",
    "owner": "dev_owner",
    "engineer": "dev_engineer",
    "reviewer": "dev_reviewer",
    "qa": "dev_qa",
    "approver": "dev_approver",
    "auditor": "dev_auditor",
}
EMAILS = {
    "engineer": "engineer@example.dev",
    "reviewer": "reviewer@example.dev",
    "qa": "qa@example.dev",
    "approver": "approver@example.dev",
    "auditor": "auditor@example.dev",
}


def H(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    from agentic_sdlc.seed import seed_all

    with session_scope() as db:
        seed_all(db)
    yield


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    from agentic_sdlc.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def project(client: TestClient) -> dict:
    """A fresh project owned by `owner`, with engineer/reviewer/qa/approver/auditor members."""
    r = client.post("/api/v1/projects", json={"name": f"Test {uuid.uuid4().hex[:6]}"}, headers=H("owner"))
    assert r.status_code == 201, r.text
    p = r.json()
    for who, role in [
        ("engineer", "engineer"),
        ("reviewer", "reviewer"),
        ("qa", "qa"),
        ("approver", "release_approver"),
        ("auditor", "auditor"),
    ]:
        rr = client.post(
            f"/api/v1/projects/{p['id']}/members",
            json={"email": EMAILS[who], "role": role},
            headers=H("owner"),
        )
        assert rr.status_code == 201, rr.text
    return p
