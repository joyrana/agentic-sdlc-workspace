"""Runtime configuration. All values come from environment variables (prefix-free) or `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[5]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://sdlc:sdlc@localhost:5432/sdlc"

    # Storage and working directories
    data_dir: Path = REPO_ROOT / "var"
    skills_dir: Path = REPO_ROOT / "skills"
    fixtures_dir: Path = REPO_ROOT / "samples"
    model_registry_path: Path = Path(__file__).resolve().parents[1] / "config" / "models.yaml"

    # Authentication
    auth_mode: Literal["token"] = "token"
    dev_seed_users: bool = True
    token_ttl_days: int = 30

    # Uploads
    max_upload_bytes: int = 10 * 1024 * 1024
    max_docx_entries: int = 2000
    max_docx_uncompressed_bytes: int = 60 * 1024 * 1024

    # Rate limiting (per token, per process)
    rate_limit_per_minute: int = 600

    # Sandbox
    sandbox_backend: Literal["namespace", "docker", "none"] = "namespace"
    sandbox_uid: int = 64000
    sandbox_masked_paths: list[str] = Field(
        default_factory=lambda: ["/root", "/etc/agentic-sdlc", "/var/lib/postgresql", "/home"]
    )
    # Toolchain available inside the sandbox (must be outside masked paths). `make sandbox-toolchain`
    # creates /opt/asw-sandbox with pytest; container images install it into the system Python.
    sandbox_python: str = "/opt/asw-sandbox/bin/python"
    # Disposable workspaces live outside every masked path so the sandbox can see them.
    workspaces_root: Path = Path("/var/tmp/asw-workspaces")  # noqa: S108 - dedicated, 0711
    sandbox_timeout_s: int = 120
    sandbox_memory_bytes: int = 2 * 1024**3
    sandbox_cpu_seconds: int = 120
    sandbox_max_processes: int = 128
    sandbox_max_file_bytes: int = 100 * 1024 * 1024
    sandbox_docker_image: str = "python:3.12-slim"

    # Model layer
    model_endpoint_url: str | None = None  # OpenAI-compatible endpoint (e.g. vLLM)
    model_endpoint_api_key: str | None = None
    allow_mock_models: bool = False  # mock provider may be routed to only when explicitly enabled

    # Deployment
    production_deploy_enabled: bool = False
    github_actions_deploy_repo: str | None = None
    github_actions_deploy_workflow: str | None = None

    # Workflow default limits
    max_repair_attempts: int = 3
    max_architecture_revisions: int = 3
    max_model_calls: int = 200
    max_tokens: int = 2_000_000
    max_cost_usd: float = 25.0
    max_wall_clock_s: int = 3600

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production" and self.dev_seed_users:
            raise ValueError("dev_seed_users must be false when APP_ENV=production")
        return self

    @property
    def objects_dir(self) -> Path:
        return self.data_dir / "objects"

    @property
    def workspaces_dir(self) -> Path:
        return self.workspaces_root

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"


@lru_cache
def get_settings() -> Settings:
    return Settings()
