"""Domain errors mapped to a consistent HTTP error envelope."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


class PolicyViolation(DomainError):
    status_code = 403
    code = "policy_violation"


class RateLimited(DomainError):
    status_code = 429
    code = "rate_limited"


class BudgetExceeded(DomainError):
    status_code = 409
    code = "budget_exceeded"
