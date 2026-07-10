from __future__ import annotations

import re
from typing import Any

from app.assistant.models import (
    PermissionClass,
    PolicyContext,
    PolicyDecision,
    ToolCallRequest,
    ToolDescriptor,
)
from app.assistant.registry import ToolRegistry, normalize_tool_name


AUTO_ALLOW_CLASSES = frozenset({PermissionClass.READ_LOCAL, PermissionClass.PROBE_NETWORK})
APPROVAL_REQUIRED_CLASSES = frozenset(
    {PermissionClass.WRITE_LOCAL, PermissionClass.WRITE_SYSTEM, PermissionClass.CONNECT_REMOTE}
)

RAW_NAME_TOKENS = frozenset(
    {
        "bash",
        "cmd",
        "cmdexe",
        "exec",
        "execute",
        "openssh",
        "plink",
        "powershell",
        "pwsh",
        "sh",
        "shell",
        "subprocess",
        "terminal",
    }
)
RAW_NAME_PHRASES = (
    "command_exec",
    "command_execution",
    "execute_command",
    "exec_command",
    "powershell_command",
    "raw_command",
    "remote_command",
    "run_command",
    "shell_command",
    "ssh_command",
)
RAW_COMPACT_MARKERS = (
    "cmdexe",
    "execcommand",
    "executecommand",
    "openssh",
    "powershell",
    "powershellcommand",
    "rawcommand",
    "rawshell",
    "remotecommand",
    "runcommand",
    "shellcommand",
    "sshcommand",
)
RAW_DESCRIPTOR_PATTERNS = (
    re.compile(r"\b(raw\s+)?(shell|powershell|pwsh|cmd(?:\.exe)?|terminal|subprocess)\b", re.IGNORECASE),
    re.compile(r"\bssh\b.*\b(command|exec|shell|session)\b", re.IGNORECASE),
    re.compile(r"\bremote\s+command\s+execution\b", re.IGNORECASE),
)


class PolicyEvaluator:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry

    def evaluate(
        self,
        request_or_tool: ToolCallRequest | ToolDescriptor | str,
        descriptor: ToolDescriptor | PolicyContext | None = None,
        context: PolicyContext | None = None,
    ) -> PolicyDecision:
        request, descriptor, context = self._coerce_inputs(request_or_tool, descriptor, context)

        if descriptor is None:
            if is_raw_tool_name(request.tool_name):
                return PolicyDecision.block(
                    "원시 셸, PowerShell 또는 SSH 방식 도구는 NetOps 어시스턴트에서 사용할 수 없습니다.",
                    blocked_by="name",
                    tool_name=request.tool_name,
                )
            return PolicyDecision.block("Tool is not registered.", tool_name=request.tool_name)

        blocked_by = raw_tool_block_reason(descriptor)
        if blocked_by:
            return PolicyDecision.block(
                "원시 셸, PowerShell 또는 SSH 방식 도구는 NetOps 어시스턴트에서 사용할 수 없습니다.",
                permission_class=descriptor.permission_class,
                blocked_by=blocked_by,
                tool_name=descriptor.name,
            )

        permission_class = descriptor.permission_class
        if permission_class == PermissionClass.WRITE_SYSTEM and descriptor.admin_required and not context.is_admin:
            return PolicyDecision.block(
                "Tool requires administrator privileges.",
                permission_class=permission_class,
                tool_name=descriptor.name,
                admin_required=True,
            )

        if permission_class in AUTO_ALLOW_CLASSES:
            return PolicyDecision.allow(
                "Permission class is auto-allowed.",
                permission_class=permission_class,
                tool_name=descriptor.name,
            )

        if permission_class in APPROVAL_REQUIRED_CLASSES:
            if context.has_approval_for(request, descriptor):
                return PolicyDecision.allow(
                    "Approval already granted.",
                    permission_class=permission_class,
                    tool_name=descriptor.name,
                    approved=True,
                )
            return PolicyDecision.require_approval(
                "Tool requires explicit approval before execution.",
                permission_class=permission_class,
                tool_name=descriptor.name,
                admin_required=descriptor.admin_required,
            )

        return PolicyDecision.block(
            "Unsupported permission class.",
            permission_class=permission_class,
            tool_name=descriptor.name,
        )

    def _coerce_inputs(
        self,
        request_or_tool: ToolCallRequest | ToolDescriptor | str,
        descriptor: ToolDescriptor | PolicyContext | None,
        context: PolicyContext | None,
    ) -> tuple[ToolCallRequest, ToolDescriptor | None, PolicyContext]:
        if isinstance(descriptor, PolicyContext) and context is None:
            context = descriptor
            descriptor = None

        if isinstance(request_or_tool, ToolDescriptor):
            descriptor = request_or_tool
            request = ToolCallRequest(tool_name=request_or_tool.name)
        elif isinstance(request_or_tool, ToolCallRequest):
            request = request_or_tool
        else:
            request = ToolCallRequest(tool_name=str(request_or_tool))

        if descriptor is None and self.registry is not None:
            descriptor = self.registry.resolve(request)
        if descriptor is not None and not isinstance(descriptor, ToolDescriptor):
            raise TypeError("descriptor must be a ToolDescriptor.")

        return request, descriptor, context or PolicyContext()


def raw_tool_block_reason(descriptor: ToolDescriptor) -> str:
    if is_raw_tool_name(descriptor.name):
        return "name"
    if any(is_raw_tool_name(alias) for alias in descriptor.aliases):
        return "alias"
    if is_raw_tool_category(descriptor.category):
        return "category"
    if _metadata_marks_raw_tool(descriptor.metadata):
        return "metadata"
    if is_raw_descriptor_text(descriptor.description):
        return "description"
    return ""


def is_raw_tool_name(name: str) -> bool:
    normalized = normalize_tool_name(name)
    if not normalized:
        return False
    compact = normalized.replace("_", "")
    return bool(
        compact == "ssh"
        or normalized in RAW_NAME_TOKENS
        or any(phrase in normalized for phrase in RAW_NAME_PHRASES)
        or any(marker in compact for marker in RAW_COMPACT_MARKERS)
    )


def is_raw_tool_category(category: str) -> bool:
    return is_raw_tool_name(category)


def is_raw_descriptor_text(text: str) -> bool:
    text = str(text or "")
    return any(pattern.search(text) for pattern in RAW_DESCRIPTOR_PATTERNS)


def _identifier_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())


def _metadata_marks_raw_tool(metadata: dict[str, Any]) -> bool:
    if not metadata:
        return False

    raw_flags = ("raw_shell", "raw_command", "uses_shell", "shell", "command_executor")
    if any(bool(metadata.get(flag)) for flag in raw_flags):
        return True

    transport = str(metadata.get("transport", "") or "").casefold()
    runner = str(metadata.get("runner", "") or "").casefold()
    return transport in {"shell", "powershell", "pwsh", "ssh"} or runner in {"shell", "powershell", "pwsh", "ssh"}


__all__ = [
    "APPROVAL_REQUIRED_CLASSES",
    "AUTO_ALLOW_CLASSES",
    "PermissionClass",
    "PolicyContext",
    "PolicyDecision",
    "PolicyEvaluator",
    "ToolCallRequest",
    "ToolDescriptor",
    "is_raw_descriptor_text",
    "is_raw_tool_category",
    "is_raw_tool_name",
    "raw_tool_block_reason",
]
