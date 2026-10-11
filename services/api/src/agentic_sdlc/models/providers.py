"""Provider implementations behind a single interface."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

import httpx

from agentic_sdlc.models.tokens import estimate_messages, estimate_tokens
from agentic_sdlc.models.types import ModelEntry, ModelError, ModelRequest, ModelResponse


class ModelProvider(Protocol):
    def generate(self, entry: ModelEntry, request: ModelRequest) -> ModelResponse: ...

    def available(self, entry: ModelEntry) -> bool: ...


class OpenAICompatibleProvider:
    """Chat Completions over HTTP (vLLM, TGI, llama.cpp server and similar).

    Structured output is requested with `response_format: json_schema`; the caller still validates
    the result because servers differ in how strictly they enforce it.
    """

    def __init__(self, base_url: str, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client or httpx.Client()
        self._served: tuple[float, set[str]] | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def available(self, entry: ModelEntry) -> bool:
        now = time.monotonic()
        if self._served is None or now - self._served[0] > 60:
            try:
                r = self.client.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
                r.raise_for_status()
                names = {m.get("id", "") for m in r.json().get("data", [])}
            except (httpx.HTTPError, ValueError):
                names = set()
            self._served = (now, names)
        return entry.served_model_name in self._served[1]

    def generate(self, entry: ModelEntry, request: ModelRequest) -> ModelResponse:
        body: dict[str, object] = {
            "model": entry.served_model_name,
            "messages": [m.model_dump() for m in request.messages],
            "max_tokens": min(request.max_output_tokens, entry.max_output_tokens),
            "temperature": request.temperature,
        }
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": request.json_schema, "strict": True},
            }
        start = time.perf_counter()
        try:
            r = self.client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
                timeout=request.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ModelError("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"model request failed: {exc.__class__.__name__}") from exc
        if r.status_code >= 500 or r.status_code == 429:
            raise ModelError(f"model server returned {r.status_code}")
        if r.status_code >= 400:
            raise ModelError(f"model server rejected request ({r.status_code})", retryable=False)
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("malformed model response") from exc
        usage = data.get("usage") or {}
        reported = "prompt_tokens" in usage and "completion_tokens" in usage
        return ModelResponse(
            text=text,
            input_tokens=int(usage["prompt_tokens"])
            if reported
            else estimate_messages([m.content for m in request.messages]),
            output_tokens=int(usage["completion_tokens"]) if reported else estimate_tokens(text),
            usage_estimated=not reported,
            latency_ms=int((time.perf_counter() - start) * 1000),
            model_ref=entry.ref,
        )


class MockProvider:
    """Deterministic in-process provider for tests and explicitly enabled demos. Never real inference.

    Responses come from a queue (scripted per test) or a responder function. Raising behaviors
    (timeouts, malformed output) can be scripted as exceptions in the queue.
    """

    def __init__(self, responder: Callable[[ModelRequest], str] | None = None) -> None:
        self.queue: deque[str | Exception] = deque()
        self.responder = responder
        self.calls: list[ModelRequest] = []

    def script(self, *responses: str | Exception) -> MockProvider:
        self.queue.extend(responses)
        return self

    def available(self, entry: ModelEntry) -> bool:
        return True

    def generate(self, entry: ModelEntry, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if self.queue:
            item = self.queue.popleft()
            if isinstance(item, Exception):
                raise item
            text = item
        elif self.responder is not None:
            text = self.responder(request)
        else:
            raise ModelError("mock provider has no scripted response", retryable=False)
        return ModelResponse(
            text=text,
            input_tokens=estimate_messages([m.content for m in request.messages]),
            output_tokens=estimate_tokens(text),
            usage_estimated=True,
            latency_ms=0,
            model_ref=entry.ref,
            is_mock=True,
        )
