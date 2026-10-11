"""Skill registry: loads manifests from the repository's skills/ directory only.

There is deliberately no API to install or modify skills at runtime: untrusted documents or model
output can never add or change a privileged capability.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_sdlc.core.errors import Conflict, NotFound
from agentic_sdlc.core.hashing import content_hash
from agentic_sdlc.core.settings import get_settings
from agentic_sdlc.orm import SkillDefinition, SkillVersion
from agentic_sdlc.skills.base import SkillImplementation
from agentic_sdlc.skills.manifest import SkillManifest
from agentic_sdlc.skills.tools import KNOWN_TOOLS

AGENTS_PATH = Path(__file__).resolve().parents[1] / "config" / "agents.yaml"


class SkillLoadError(Exception):
    pass


def load_agents(path: Path = AGENTS_PATH) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    agents: dict[str, dict[str, Any]] = data["agents"]
    for name, a in agents.items():
        unknown = set(a.get("tools", [])) - KNOWN_TOOLS
        if unknown:
            raise SkillLoadError(f"agent {name} declares unknown tools {sorted(unknown)}")
    return agents


def _import_impl(path: str) -> SkillImplementation:
    module_name, _, cls_name = path.partition(":")
    if not module_name.startswith("agentic_sdlc.skills_impl."):
        raise SkillLoadError(f"implementation must live under agentic_sdlc.skills_impl: {path}")
    cls = getattr(importlib.import_module(module_name), cls_name)
    if not (isinstance(cls, type) and issubclass(cls, SkillImplementation)):
        raise SkillLoadError(f"{path} is not a SkillImplementation")
    return cls()


class SkillRegistry:
    def __init__(self, manifests: list[SkillManifest], agents: dict[str, dict[str, Any]]) -> None:
        self.agents = agents
        self.manifests: dict[str, SkillManifest] = {}
        self.impls: dict[str, SkillImplementation] = {}
        for m in manifests:
            if m.id in self.manifests:
                raise SkillLoadError(f"duplicate skill id {m.id}")
            self._validate(m)
            self.manifests[m.id] = m
            self.impls[m.id] = _import_impl(m.implementation)
            impl = self.impls[m.id]
            missing = set(m.strategies) - impl.strategies
            if missing:
                raise SkillLoadError(f"{m.ref}: implementation lacks strategies {sorted(missing)}")
            for name in m.preconditions:
                if name not in impl.preconditions():
                    raise SkillLoadError(f"{m.ref}: unknown precondition '{name}'")
            for name in m.postconditions:
                if name not in impl.postconditions():
                    raise SkillLoadError(f"{m.ref}: unknown postcondition '{name}'")

    def _validate(self, m: SkillManifest) -> None:
        for label, schema in (("input", m.input_schema), ("output", m.output_schema)):
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise SkillLoadError(f"{m.ref}: invalid {label} schema: {exc.message}") from exc
        unknown = set(m.tools) - KNOWN_TOOLS
        if unknown:
            raise SkillLoadError(f"{m.ref}: unknown tools {sorted(unknown)}")
        for agent in m.agents:
            if agent not in self.agents:
                raise SkillLoadError(f"{m.ref}: unknown agent {agent}")
            if m.id not in self.agents[agent].get("skills", []):
                raise SkillLoadError(f"{m.ref}: agent {agent} does not list this skill")
        if "llm" in m.strategies and m.model is None:
            raise SkillLoadError(f"{m.ref}: llm strategy requires model requirements")

    @classmethod
    def from_directory(cls, root: Path, agents_path: Path = AGENTS_PATH) -> SkillRegistry:
        manifests = []
        for path in sorted(root.glob("*/*/skill.yaml")):
            try:
                data = yaml.safe_load(path.read_text())
                manifests.append(SkillManifest.model_validate(data))
            except Exception as exc:
                raise SkillLoadError(f"{path}: {exc}") from exc
        return cls(manifests, load_agents(agents_path))

    def get(self, skill_id: str) -> SkillManifest:
        if skill_id not in self.manifests:
            raise NotFound(f"skill {skill_id} not found")
        return self.manifests[skill_id]

    def discover(
        self,
        *,
        intent: str | None = None,
        document_kind: str | None = None,
        stage: str | None = None,
        agent: str | None = None,
    ) -> list[SkillManifest]:
        """Deterministic discovery by declared triggers."""
        out = []
        for m in self.manifests.values():
            t = m.triggers
            if intent and intent not in t.intents:
                continue
            if document_kind and document_kind not in t.document_kinds:
                continue
            if stage and stage not in t.workflow_stages:
                continue
            if agent and agent not in m.agents:
                continue
            out.append(m)
        return sorted(out, key=lambda m: m.id)

    def sync_to_db(self, db: Session) -> None:
        """Mirror manifests into the database. A changed manifest must bump its version."""
        for m in self.manifests.values():
            manifest = m.model_dump()
            digest = content_hash(manifest)
            definition = db.scalar(select(SkillDefinition).where(SkillDefinition.skill_id == m.id))
            if definition is None:
                definition = SkillDefinition(
                    skill_id=m.id, name=m.name, category=m.category, latest_version=m.version
                )
                db.add(definition)
                db.flush()
            existing = db.scalar(
                select(SkillVersion).where(SkillVersion.skill_id == m.id, SkillVersion.version == m.version)
            )
            if existing is None:
                db.add(
                    SkillVersion(skill_id=m.id, version=m.version, manifest=manifest, manifest_hash=digest)
                )
                definition.latest_version = m.version
                definition.name = m.name
            elif existing.manifest_hash != digest:
                raise Conflict(
                    f"skill {m.ref} changed without a version bump",
                    details={"registered_hash": existing.manifest_hash, "new_hash": digest},
                )


@lru_cache
def get_skill_registry() -> SkillRegistry:
    import agentic_sdlc.skills_impl  # noqa: F401  (registers tools)

    return SkillRegistry.from_directory(get_settings().skills_dir)
