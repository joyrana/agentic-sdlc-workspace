from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Pricing(BaseModel):
    input_per_1k_usd: float = 0.0
    output_per_1k_usd: float = 0.0


class ModelEntry(BaseModel):
    model_id: str
    provider: Literal["openai_compatible", "mock"]
    served_model_name: str
    revision: str
    family: str
    license: str
    license_verified: bool = False
    deployment: dict[str, Any] = Field(default_factory=dict)
    tokenizer: str
    context_window: int
    max_output_tokens: int
    tasks: list[str]
    capabilities: list[str]
    privacy: Literal["self_hosted", "in_process", "external"]
    pricing: Pricing = Field(default_factory=Pricing)
    benchmarks: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    is_mock: bool = False

    @property
    def ref(self) -> str:
        return f"{self.model_id}@{self.revision}"

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            input_tokens / 1000 * self.pricing.input_per_1k_usd
            + output_tokens / 1000 * self.pricing.output_per_1k_usd,
            6,
        )


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ModelRequest(BaseModel):
    messages: list[Message]
    json_schema: dict[str, Any] | None = None
    max_output_tokens: int = 4096
    temperature: float = 0.0
    timeout_s: float = 120.0


class ModelResponse(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    usage_estimated: bool
    latency_ms: int
    model_ref: str
    is_mock: bool = False


class ModelError(Exception):
    """Provider failure (network, timeout, malformed response)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable
