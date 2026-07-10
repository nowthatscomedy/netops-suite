from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionClass(str, Enum):
    READ_LOCAL = "read_local"
    PROBE_NETWORK = "probe_network"
    WRITE_LOCAL = "write_local"
    WRITE_SYSTEM = "write_system"
    CONNECT_REMOTE = "connect_remote"

    @classmethod
    def from_value(cls, value: "PermissionClass | str") -> "PermissionClass":
        if isinstance(value, cls):
            return value

        normalized = str(value or "").strip().casefold().replace("-", "_")
        aliases = {
            "read": cls.READ_LOCAL,
            "local_read": cls.READ_LOCAL,
            "probe": cls.PROBE_NETWORK,
            "network_probe": cls.PROBE_NETWORK,
            "write": cls.WRITE_LOCAL,
            "local_write": cls.WRITE_LOCAL,
            "system_write": cls.WRITE_SYSTEM,
            "remote": cls.CONNECT_REMOTE,
            "remote_connect": cls.CONNECT_REMOTE,
        }
        if normalized in aliases:
            return aliases[normalized]
        for member in cls:
            if normalized in {member.name.casefold(), member.value}:
                return member
        raise ValueError(f"Unknown permission class: {value!r}")


@dataclass(slots=True)
class ToolDescriptor:
    name: str
    permission_class: PermissionClass | str
    display_name: str = ""
    description: str = ""
    category: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    admin_required: bool = False
    approval_required: bool | None = None
    risk_level: str = "low"
    impact: str = ""
    reversibility: str = ""
    timeout_seconds: int = 30
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        if not self.name:
            raise ValueError("ToolDescriptor.name is required.")
        self.permission_class = PermissionClass.from_value(self.permission_class)
        self.display_name = str(self.display_name or "").strip()
        self.description = str(self.description or "").strip()
        self.category = str(self.category or "").strip()
        self.input_schema = dict(self.input_schema or {})
        self.output_schema = dict(self.output_schema or {})
        self.admin_required = bool(self.admin_required)
        if self.approval_required is not None:
            self.approval_required = bool(self.approval_required)
        self.risk_level = str(self.risk_level or "low").strip()
        self.impact = str(self.impact or "").strip()
        self.reversibility = str(self.reversibility or "").strip()
        self.timeout_seconds = max(1, int(self.timeout_seconds or 30))
        self.aliases = _string_tuple(self.aliases)
        self.tags = _string_tuple(self.tags)
        self.metadata = dict(self.metadata or {})

    @property
    def permission(self) -> PermissionClass:
        return self.permission_class

    def requires_approval_by_default(self) -> bool:
        if self.approval_required is not None:
            return self.approval_required
        return self.permission_class in {
            PermissionClass.WRITE_LOCAL,
            PermissionClass.WRITE_SYSTEM,
            PermissionClass.CONNECT_REMOTE,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolDescriptor":
        return cls(
            name=str(data.get("name", "") or ""),
            permission_class=data.get("permission_class", data.get("permission", PermissionClass.READ_LOCAL)),
            display_name=str(data.get("display_name", "") or ""),
            description=str(data.get("description", "") or ""),
            category=str(data.get("category", "") or ""),
            input_schema=dict(data.get("input_schema", {}) or {}),
            output_schema=dict(data.get("output_schema", {}) or {}),
            admin_required=bool(data.get("admin_required", data.get("adminRequired", False))),
            approval_required=data.get("approval_required"),
            risk_level=str(data.get("risk_level", "low") or "low"),
            impact=str(data.get("impact", "") or ""),
            reversibility=str(data.get("reversibility", "") or ""),
            timeout_seconds=int(data.get("timeout_seconds", 30) or 30),
            aliases=_string_tuple(data.get("aliases", ())),
            tags=_string_tuple(data.get("tags", ())),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "permission_class": self.permission_class.value,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "admin_required": self.admin_required,
            "approval_required": self.approval_required,
            "risk_level": self.risk_level,
            "impact": self.impact,
            "reversibility": self.reversibility,
            "timeout_seconds": self.timeout_seconds,
            "aliases": list(self.aliases),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ToolCallRequest:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    actor: str = ""
    approved: bool = False
    user_intent: str = ""
    source: str = "assistant"
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tool_name = str(self.tool_name or "").strip()
        if not self.tool_name:
            raise ValueError("ToolCallRequest.tool_name is required.")
        self.arguments = dict(self.arguments or {})
        self.call_id = str(self.call_id or "").strip()
        self.actor = str(self.actor or "").strip()
        self.approved = bool(self.approved)
        self.user_intent = str(self.user_intent or "").strip()
        self.source = str(self.source or "assistant").strip()
        self.session_id = str(self.session_id or "").strip()
        self.metadata = dict(self.metadata or {})

    @property
    def name(self) -> str:
        return self.tool_name

    @property
    def args(self) -> dict[str, Any]:
        return self.arguments

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCallRequest":
        return cls(
            tool_name=str(data.get("tool_name", data.get("name", "")) or ""),
            arguments=dict(data.get("arguments", {}) or {}),
            call_id=str(data.get("call_id", data.get("id", "")) or ""),
            actor=str(data.get("actor", data.get("user_id", "")) or ""),
            approved=bool(data.get("approved", False)),
            user_intent=str(data.get("user_intent", "") or ""),
            source=str(data.get("source", "assistant") or "assistant"),
            session_id=str(data.get("session_id", "") or ""),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "call_id": self.call_id,
            "actor": self.actor,
            "approved": self.approved,
            "user_intent": self.user_intent,
            "source": self.source,
            "session_id": self.session_id,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ToolResult:
    success: bool
    output: str = ""
    error: str = ""
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.success = bool(self.success)
        self.output = str(self.output or "")
        self.error = str(self.error or "")
        self.metadata = dict(self.metadata or {})

    @classmethod
    def ok(cls, output: str = "", payload: Any = None, **metadata: Any) -> "ToolResult":
        return cls(True, output=output, payload=payload, metadata=metadata)

    @classmethod
    def failed(cls, error: str, output: str = "", payload: Any = None, **metadata: Any) -> "ToolResult":
        return cls(False, output=output, error=error, payload=payload, metadata=metadata)

    @classmethod
    def from_operation_result(cls, result: Any) -> "ToolResult":
        success = bool(getattr(result, "success", False))
        message = str(getattr(result, "message", "") or "")
        details = str(getattr(result, "details", "") or "")
        payload = getattr(result, "payload", None)
        metadata = {"message": message, "details": details, "status": "ok" if success else "error"}
        output = _join_output(message, details)
        if success:
            return cls.ok(output, payload=payload, **metadata)
        return cls.failed(message or "Operation failed.", output=details, payload=payload, **metadata)

    @property
    def message(self) -> str:
        value = self.metadata.get("message")
        if value is not None:
            return str(value)
        return self.output or self.error

    @property
    def details(self) -> str:
        value = self.metadata.get("details")
        if value is not None:
            return str(value)
        return self.error

    @property
    def data(self) -> Any:
        return self.payload

    @property
    def status(self) -> str:
        value = self.metadata.get("status")
        if value is not None:
            return str(value)
        return "ok" if self.success else "error"

    def to_text(self) -> str:
        return _join_output(self.message, self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "payload": self.payload,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class PolicyContext:
    is_admin: bool = False
    actor: str = ""
    approved: bool = False
    approval_granted: bool = False
    approved_tools: tuple[str, ...] = ()
    approved_categories: tuple[str, ...] = ()
    approved_call_ids: frozenset[str] = frozenset()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.is_admin = bool(self.is_admin)
        self.actor = str(self.actor or "").strip()
        self.approved = bool(self.approved)
        self.approval_granted = bool(self.approval_granted)
        self.approved_tools = _casefold_tuple(self.approved_tools)
        self.approved_categories = _casefold_tuple(self.approved_categories)
        self.approved_call_ids = frozenset(str(item).strip() for item in self.approved_call_ids if str(item).strip())
        self.metadata = dict(self.metadata or {})

    def has_approval_for(self, request: ToolCallRequest, descriptor: ToolDescriptor) -> bool:
        if self.approved or self.approval_granted or request.approved:
            return True
        if request.call_id and request.call_id in self.approved_call_ids:
            return True
        tool_names = {descriptor.name.casefold(), request.tool_name.casefold(), *[alias.casefold() for alias in descriptor.aliases]}
        categories = {descriptor.category.casefold()} if descriptor.category else set()
        return bool(tool_names.intersection(self.approved_tools) or categories.intersection(self.approved_categories))

    def is_approved(self, request: ToolCallRequest) -> bool:
        return self.approved or self.approval_granted or bool(request.call_id and request.call_id in self.approved_call_ids)


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
    permission_class: PermissionClass | None = None
    blocked: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.allowed = bool(self.allowed)
        self.requires_approval = bool(self.requires_approval)
        self.reason = str(self.reason or "")
        if self.permission_class is not None:
            self.permission_class = PermissionClass.from_value(self.permission_class)
        self.blocked = bool(self.blocked)
        self.metadata = dict(self.metadata or {})

    @property
    def status(self) -> str:
        if self.allowed:
            return "allowed"
        if self.blocked:
            return "blocked"
        if self.requires_approval:
            return "requires_approval"
        return "denied"

    @property
    def approval_required(self) -> bool:
        return self.requires_approval

    @property
    def permission(self) -> PermissionClass | None:
        return self.permission_class

    @classmethod
    def allow(
        cls,
        reason: str = "",
        permission_class: PermissionClass | None = None,
        **metadata: Any,
    ) -> "PolicyDecision":
        return cls(True, reason=reason, permission_class=permission_class, metadata=metadata)

    @classmethod
    def require_approval(
        cls,
        reason: str,
        permission_class: PermissionClass | None = None,
        **metadata: Any,
    ) -> "PolicyDecision":
        return cls(False, requires_approval=True, reason=reason, permission_class=permission_class, metadata=metadata)

    @classmethod
    def block(
        cls,
        reason: str,
        permission_class: PermissionClass | None = None,
        **metadata: Any,
    ) -> "PolicyDecision":
        return cls(False, reason=reason, permission_class=permission_class, blocked=True, metadata=metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "blocked": self.blocked,
            "status": self.status,
            "reason": self.reason,
            "permission_class": self.permission_class.value if self.permission_class else None,
            "metadata": dict(self.metadata),
        }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = (value,)
    else:
        candidates = tuple(value)
    return tuple(str(item).strip() for item in candidates if str(item).strip())


def _casefold_tuple(value: Any) -> tuple[str, ...]:
    return tuple(item.casefold() for item in _string_tuple(value))


def _join_output(message: str, details: str = "") -> str:
    return "\n".join(part for part in (str(message or "").strip(), str(details or "").strip()) if part)


__all__ = [
    "PermissionClass",
    "PolicyContext",
    "PolicyDecision",
    "ToolCallRequest",
    "ToolDescriptor",
    "ToolResult",
]
