from __future__ import annotations

from enum import Enum

from app.assistant.models import (
    PermissionClass,
    PolicyContext,
    PolicyDecision,
    ToolCallRequest,
    ToolDescriptor,
    ToolResult,
)


class PolicyDecisionStatus(str, Enum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


__all__ = [
    "PermissionClass",
    "PolicyContext",
    "PolicyDecision",
    "PolicyDecisionStatus",
    "ToolCallRequest",
    "ToolDescriptor",
    "ToolResult",
]
