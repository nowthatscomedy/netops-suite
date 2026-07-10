from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app.assistant.models import PolicyContext, PolicyDecision, ToolCallRequest, ToolDescriptor, ToolResult


REDACTED = "[redacted]"
REDACTED_COMMAND = "[redacted command]"
RAW_COMMAND_KEYS = {"cmd", "command", "powershell", "script", "shell", "ssh_command"}
SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|auth(?:orization)?|bearer|cookie|credential|key|pass(?:word|phrase|wd)?|private[_-]?key|secret|session|token)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]+)", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(
    r"\b(api[_-]?key|authorization|cookie|password|passwd|passphrase|secret|session|token)\b(\s*[:=]\s*)([^\s,;&]+)",
    re.IGNORECASE,
)


class AuditLogger:
    def __init__(
        self,
        log_path: str | Path | None = None,
        logger: logging.Logger | None = None,
        max_string_length: int = 4096,
    ) -> None:
        self.log_path = Path(log_path) if log_path else None
        self.logger = logger
        self.max_string_length = max(256, int(max_string_length))
        self._events: list[dict[str, Any]] = []

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def redact_payload(self, payload: Any) -> Any:
        return self.redact(payload)

    def sanitize_payload(self, payload: Any) -> Any:
        return self.redact(payload)

    def sanitize(self, payload: Any) -> Any:
        return self.redact(payload)

    def redact(self, payload: Any) -> Any:
        return self._redact_value(payload, key_hint="", depth=0)

    def log_event(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": str(event_type or "event"),
            "payload": self.redact(dict(payload)),
        }
        self._events.append(event)
        self._write_event(event)
        return event

    def record_policy_decision(
        self,
        request: ToolCallRequest,
        descriptor: ToolDescriptor | None,
        decision: PolicyDecision,
        context: PolicyContext | None = None,
    ) -> dict[str, Any]:
        return self.log_event(
            "policy_decision",
            {
                "request": request,
                "descriptor": descriptor,
                "decision": decision,
                "context": context,
            },
        )

    def log_policy_decision(
        self,
        request: ToolCallRequest,
        descriptor: ToolDescriptor | None,
        decision: PolicyDecision,
        context: PolicyContext | None = None,
    ) -> dict[str, Any]:
        return self.record_policy_decision(request, descriptor, decision, context)

    def record_decision(self, decision: Any, *, call: Any = None, result: Any = None) -> dict[str, Any]:
        return self.log_event("policy_decision", {"call": call, "decision": decision, "result": result})

    def record_tool_call(
        self,
        request: ToolCallRequest,
        descriptor: ToolDescriptor | None = None,
        decision: PolicyDecision | None = None,
    ) -> dict[str, Any]:
        return self.log_event(
            "tool_call",
            {
                "request": request,
                "descriptor": descriptor,
                "decision": decision,
            },
        )

    def record_tool_result(
        self,
        request: ToolCallRequest,
        result: ToolResult,
        descriptor: ToolDescriptor | None = None,
    ) -> dict[str, Any]:
        return self.log_event(
            "tool_result",
            {
                "request": request,
                "descriptor": descriptor,
                "result": result,
            },
        )

    def _write_event(self, event: dict[str, Any]) -> None:
        encoded = json.dumps(event, ensure_ascii=False, default=str)
        if self.logger is not None:
            self.logger.info("assistant_audit %s", encoded)

        if self.log_path is None:
            return

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        except OSError as exc:
            if self.logger is not None:
                self.logger.warning("assistant audit log write failed: %s", exc)

    def _redact_value(self, value: Any, *, key_hint: str, depth: int) -> Any:
        if depth > 12:
            return REDACTED

        normalized_key = key_hint.casefold()
        if normalized_key in RAW_COMMAND_KEYS:
            return REDACTED_COMMAND
        if SENSITIVE_KEY_RE.search(normalized_key):
            return REDACTED

        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return {
                item.name: self._redact_value(getattr(value, item.name), key_hint=item.name, depth=depth + 1)
                for item in fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): self._redact_value(nested, key_hint=str(key), depth=depth + 1)
                for key, nested in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._redact_value(item, key_hint="", depth=depth + 1) for item in value]
        if isinstance(value, str):
            return self._redact_string(value)
        return self._redact_string(str(value))

    def _redact_string(self, value: str) -> str:
        lowered = value.casefold()
        if "-----begin private key-----" in lowered:
            return REDACTED
        redacted = BEARER_RE.sub(f"Bearer {REDACTED}", value)
        redacted = ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", redacted)
        if len(redacted) > self.max_string_length:
            return redacted[: self.max_string_length] + "...[truncated]"
        return redacted


__all__ = ["AuditLogger", "REDACTED", "REDACTED_COMMAND"]
