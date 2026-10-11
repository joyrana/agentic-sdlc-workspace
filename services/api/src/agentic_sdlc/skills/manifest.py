"""Skill manifest contract (skills/<category>/<skill-id>/skill.yaml)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
Strategy = Literal["llm", "heuristic", "scripted"]


class ModelRequirements(BaseModel):
    task: str
    capabilities: list[str] = Field(default_factory=list)
    expected_output_tokens: int = 2048


class Budgets(BaseModel):
    max_input_tokens: int = 60_000
    max_output_tokens: int = 8_000
    max_model_calls: int = 3


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=2, ge=1, le=5)
    backoff_s: float = Field(default=0.5, ge=0, le=30)


class FailurePolicy(BaseModel):
    on_validation_error: Literal["retry_then_fail", "fail"] = "retry_then_fail"
    on_timeout: Literal["fail", "escalate"] = "fail"


class EvaluationSpec(BaseModel):
    suite: str | None = None
    success_criteria: dict[str, float] = Field(default_factory=dict)


class Triggers(BaseModel):
    intents: list[str] = Field(default_factory=list)
    document_kinds: list[str] = Field(default_factory=list)
    workflow_stages: list[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    id: str
    version: str
    name: str
    description: str
    category: str
    triggers: Triggers = Field(default_factory=Triggers)
    agents: list[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_context: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    strategies: list[Strategy]
    model: ModelRequirements | None = None
    budgets: Budgets = Field(default_factory=Budgets)
    timeout_s: int = Field(default=120, ge=1, le=3600)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    approval_level: Literal["none", "human_review", "human_approval"] = "none"
    failure: FailurePolicy = Field(default_factory=FailurePolicy)
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)
    implementation: str
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9-]{2,79}$", v):
            raise ValueError("skill id must be kebab-case")
        return v

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER.match(v):
            raise ValueError("version must be semantic (MAJOR.MINOR.PATCH)")
        return v

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"
