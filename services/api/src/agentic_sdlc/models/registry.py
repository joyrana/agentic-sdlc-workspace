"""Model registry loaded from configuration and mirrored into the database with observed statistics."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.models.providers import MockProvider, ModelProvider, OpenAICompatibleProvider
from agentic_sdlc.models.types import ModelEntry
from agentic_sdlc.orm import ModelRegistryEntry


class ModelRegistry:
    def __init__(self, entries: list[ModelEntry], providers: dict[str, ModelProvider]) -> None:
        ids = [e.model_id for e in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate model_id in registry")
        self.entries = {e.model_id: e for e in entries}
        self.providers = providers

    @classmethod
    def from_file(cls, path: Path, providers: dict[str, ModelProvider] | None = None) -> ModelRegistry:
        data = yaml.safe_load(path.read_text())
        entries = [ModelEntry.model_validate(m) for m in data.get("models", [])]
        return cls(entries, providers or default_providers())

    def get(self, model_id: str) -> ModelEntry:
        return self.entries[model_id]

    def provider_for(self, entry: ModelEntry) -> ModelProvider | None:
        return self.providers.get(entry.provider)

    def is_available(self, entry: ModelEntry) -> bool:
        provider = self.provider_for(entry)
        if provider is None:
            return False
        if entry.is_mock and not get_settings().allow_mock_models:
            return False
        return provider.available(entry)

    def sync_to_db(self, db: Session) -> None:
        for entry in self.entries.values():
            row = db.scalar(select(ModelRegistryEntry).where(ModelRegistryEntry.model_id == entry.model_id))
            data = entry.model_dump()
            if row is None:
                db.add(ModelRegistryEntry(model_id=entry.model_id, entry=data, observed={}))
            elif row.entry != data:
                row.entry = data


def default_providers() -> dict[str, ModelProvider]:
    s = get_settings()
    providers: dict[str, ModelProvider] = {"mock": MockProvider()}
    if s.model_endpoint_url:
        providers["openai_compatible"] = OpenAICompatibleProvider(
            s.model_endpoint_url, s.model_endpoint_api_key
        )
    return providers


@lru_cache
def get_registry() -> ModelRegistry:
    return ModelRegistry.from_file(get_settings().model_registry_path)


def record_observation(db: Session, model_id: str, *, success: bool, latency_ms: int) -> None:
    """Update observed reliability statistics used by the router."""
    row = db.scalar(select(ModelRegistryEntry).where(ModelRegistryEntry.model_id == model_id))
    if row is None:
        return
    obs: dict[str, Any] = dict(row.observed or {})
    calls = int(obs.get("calls", 0)) + 1
    successes = int(obs.get("successes", 0)) + (1 if success else 0)
    total_latency = int(obs.get("total_latency_ms", 0)) + latency_ms
    row.observed = {
        "calls": calls,
        "successes": successes,
        "failures": calls - successes,
        "success_rate": round(successes / calls, 4),
        "total_latency_ms": total_latency,
        "mean_latency_ms": round(total_latency / calls, 1),
    }
