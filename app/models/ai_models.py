from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


KNOWN_AI_PROVIDERS = ("codex", "claude", "gemini")


@dataclass(slots=True)
class AiProviderConfig:
    key: str
    enabled: bool = True
    command_path: str = ""
    model: str = ""
    reasoning_effort: str = ""
    speed: str = ""
    role_prompt: str = ""
    extra_args: list[str] = field(default_factory=list)
    timeout_seconds: int = 900

    @classmethod
    def from_dict(cls, key: str, data: Any) -> "AiProviderConfig":
        payload = data if isinstance(data, dict) else {}
        extra_args = payload.get("extra_args", [])
        if not isinstance(extra_args, list):
            extra_args = []
        try:
            timeout_seconds = int(payload.get("timeout_seconds", 900) or 900)
        except (TypeError, ValueError):
            timeout_seconds = 900
        return cls(
            key=key,
            enabled=bool(payload.get("enabled", True)),
            command_path=str(payload.get("command_path", "") or ""),
            model=str(payload.get("model", "") or ""),
            reasoning_effort=str(payload.get("reasoning_effort", "") or ""),
            speed=str(payload.get("speed", "") or ""),
            role_prompt=str(payload.get("role_prompt", "") or ""),
            extra_args=[str(item) for item in extra_args if str(item).strip()],
            timeout_seconds=max(30, min(timeout_seconds, 7200)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "command_path": self.command_path,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "speed": self.speed,
            "role_prompt": self.role_prompt,
            "extra_args": list(self.extra_args),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(slots=True)
class CliInvocation:
    provider_key: str
    program: str
    args: list[str]
    stdin_text: str = ""
    working_dir: str = ""
    timeout_seconds: int = 900


def default_ai_chat_config() -> dict[str, Any]:
    return {
        "version": 1,
        "active_provider": "codex",
        "auto_export": False,
        "providers": {
            "codex": {
                "enabled": True,
                "command_path": "",
                "model": "",
                "reasoning_effort": "",
                "speed": "",
                "role_prompt": "",
                "extra_args": [],
                "timeout_seconds": 900,
            },
            "claude": {
                "enabled": True,
                "command_path": "",
                "model": "",
                "reasoning_effort": "",
                "speed": "",
                "role_prompt": "",
                "extra_args": [],
                "timeout_seconds": 900,
            },
            "gemini": {
                "enabled": True,
                "command_path": "",
                "model": "",
                "reasoning_effort": "",
                "speed": "",
                "role_prompt": "",
                "extra_args": [],
                "timeout_seconds": 900,
            },
        },
    }


def normalize_ai_chat_config(config: Any) -> dict[str, Any]:
    normalized = default_ai_chat_config()
    if not isinstance(config, dict):
        return normalized

    active_provider = str(config.get("active_provider", "") or "")
    if not active_provider and isinstance(config.get("orchestration"), dict):
        active_provider = str(config["orchestration"].get("chair_provider", "") or "")
    if active_provider in KNOWN_AI_PROVIDERS:
        normalized["active_provider"] = active_provider
    normalized["auto_export"] = bool(config.get("auto_export", normalized["auto_export"]))
    if isinstance(config.get("orchestration"), dict):
        normalized["auto_export"] = bool(config["orchestration"].get("auto_export", normalized["auto_export"]))

    providers = config.get("providers", {})
    if isinstance(providers, dict):
        for key in KNOWN_AI_PROVIDERS:
            merged = dict(normalized["providers"][key])
            incoming = providers.get(key, {})
            if isinstance(incoming, dict):
                merged.update(incoming)
            normalized["providers"][key] = AiProviderConfig.from_dict(key, merged).to_dict()

    return normalized
