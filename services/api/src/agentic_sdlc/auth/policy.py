"""Role → permission policy. Deny by default: a permission not listed for a role is not granted.

Approval decisions are *not* in this table; gate policies live in `approvals.policy` and also
require a human principal.
"""

from __future__ import annotations

from typing import Final

READ_ALL: Final = {
    "project.read",
    "document.read",
    "requirement.read",
    "backlog.read",
    "architecture.read",
    "workflow.read",
    "quality.read",
    "release.read",
}

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    "project_owner": frozenset(
        READ_ALL
        | {
            "project.update",
            "project.members.manage",
            "repository.manage",
            "document.upload",
            "requirement.edit",
            "question.resolve",
            "backlog.edit",
            "architecture.edit",
            "workflow.start",
            "workflow.cancel",
            "audit.read",
        }
    ),
    "engineer": frozenset(
        READ_ALL
        | {
            "document.upload",
            "requirement.edit",
            "question.resolve",
            "backlog.edit",
            "architecture.edit",
            "workflow.start",
        }
    ),
    "reviewer": frozenset(READ_ALL | {"requirement.edit", "question.resolve"}),
    "qa": frozenset(READ_ALL | {"defect.edit"}),
    "release_approver": frozenset(READ_ALL),
    "auditor": frozenset(READ_ALL | {"audit.read"}),
}

# Global administrators manage users and memberships and can read everything for support,
# but they gain no approval authority and no write access to delivery artifacts.
ADMIN_PERMISSIONS: Final = frozenset(READ_ALL | {"project.members.manage", "audit.read"})


def permissions_for(roles: set[str], *, is_admin: bool) -> frozenset[str]:
    granted: set[str] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    if is_admin:
        granted |= ADMIN_PERMISSIONS
    return frozenset(granted)
