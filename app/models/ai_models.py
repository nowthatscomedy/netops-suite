from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


KNOWN_AI_PROVIDERS = ("codex", "claude", "gemini")
AI_MODEL_CATALOG_SOURCES = ("live", "cache", "fallback", "custom")
AI_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
AI_INPUT_MODALITIES = ("text", "image")
AI_MODEL_SPEED_TIERS = ("fast",)
AI_MODEL_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _strict_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"AI model descriptor contains an invalid {field_name} list")
    return [item.strip() for item in value if item.strip()]


@dataclass(slots=True)
class AiModelDescriptor:
    id: str
    model: str
    display_name: str = ""
    supported_reasoning_efforts: list[str] = field(default_factory=list)
    default_reasoning_effort: str = ""
    input_modalities: list[str] = field(default_factory=lambda: ["text", "image"])
    speed_tiers: list[str] = field(default_factory=list)
    is_default: bool = False
    hidden: bool = False
    upgrade: str = ""
    availability_message: str = ""
    source: str = "live"

    @classmethod
    def from_dict(cls, data: Any) -> "AiModelDescriptor":
        if not isinstance(data, dict):
            raise ValueError("AI model descriptor must be an object")

        model_id = data.get("id", data.get("model", ""))
        model = data.get("model", model_id)
        display_name = data.get("display_name", data.get("displayName", ""))
        default_reasoning = data.get("default_reasoning_effort", data.get("defaultReasoningEffort", ""))
        upgrade = data.get("upgrade", "")
        upgrade_info = data.get("upgradeInfo")
        if upgrade_info is not None and not isinstance(upgrade_info, dict):
            raise ValueError("AI model descriptor contains invalid upgrade info")
        if not upgrade and isinstance(upgrade_info, dict):
            upgrade = upgrade_info.get("model", "")
        availability_message = data.get("availability_message", "")
        availability_nux = data.get("availabilityNux")
        if availability_nux is not None and not isinstance(availability_nux, dict):
            raise ValueError("AI model descriptor contains invalid availability info")
        if isinstance(availability_nux, dict) and not availability_message:
            availability_message = availability_nux.get("message", "")
        source = data.get("source", "live")
        string_fields = (
            model_id,
            model,
            display_name,
            default_reasoning,
            upgrade,
            availability_message,
            source,
        )
        if not all(isinstance(item, str) for item in string_fields):
            raise ValueError("AI model descriptor contains an invalid string field")
        if model_id != model_id.strip() or model != model.strip():
            raise ValueError("AI model descriptor id and model must not contain surrounding whitespace")
        if not AI_MODEL_VALUE_RE.fullmatch(model_id) or not AI_MODEL_VALUE_RE.fullmatch(model):
            raise ValueError("AI model descriptor contains an invalid id or model")

        raw_reasoning = data.get(
            "supported_reasoning_efforts",
            data.get("reasoning_efforts", data.get("supportedReasoningEfforts", [])),
        )
        reasoning_efforts = _strict_string_list(raw_reasoning, "reasoning effort")
        if any(item not in AI_REASONING_EFFORTS for item in reasoning_efforts):
            raise ValueError("AI model descriptor contains an unsupported reasoning effort")
        default_reasoning = default_reasoning.strip()
        if default_reasoning and default_reasoning not in AI_REASONING_EFFORTS:
            raise ValueError("AI model descriptor contains an unsupported default reasoning effort")
        if default_reasoning and default_reasoning not in reasoning_efforts:
            raise ValueError("AI model descriptor default reasoning effort is not supported")

        input_modalities = _strict_string_list(
            data.get("input_modalities", data.get("modalities", ["text", "image"])),
            "input modality",
        )
        if any(item not in AI_INPUT_MODALITIES for item in input_modalities):
            raise ValueError("AI model descriptor contains an unsupported input modality")
        if not input_modalities:
            raise ValueError("AI model descriptor requires at least one input modality")

        speed_tiers = _strict_string_list(
            data.get(
                "speed_tiers",
                data.get("speed_options", data.get("speeds", data.get("additionalSpeedTiers", []))),
            ),
            "speed tier",
        )
        if any(item not in AI_MODEL_SPEED_TIERS for item in speed_tiers):
            raise ValueError("AI model descriptor contains an unsupported speed tier")

        source = source.strip()
        if source not in AI_MODEL_CATALOG_SOURCES:
            raise ValueError("AI model descriptor contains an invalid source")

        is_default = data.get("is_default", data.get("isDefault", False))
        hidden = data.get("hidden", False)
        if not isinstance(is_default, bool) or not isinstance(hidden, bool):
            raise ValueError("AI model descriptor contains an invalid boolean field")

        return cls(
            id=model_id,
            model=model,
            display_name=display_name.strip() or model,
            supported_reasoning_efforts=list(dict.fromkeys(reasoning_efforts)),
            default_reasoning_effort=default_reasoning,
            input_modalities=list(dict.fromkeys(input_modalities)),
            speed_tiers=list(dict.fromkeys(speed_tiers)),
            is_default=is_default,
            hidden=hidden,
            upgrade=upgrade.strip(),
            availability_message=availability_message.strip(),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "display_name": self.display_name,
            "supported_reasoning_efforts": list(self.supported_reasoning_efforts),
            "default_reasoning_effort": self.default_reasoning_effort,
            "input_modalities": list(self.input_modalities),
            "speed_tiers": list(self.speed_tiers),
            "is_default": self.is_default,
            "hidden": self.hidden,
            "upgrade": self.upgrade,
            "availability_message": self.availability_message,
            "source": self.source,
        }

    @property
    def model_id(self) -> str:
        return self.id

    @property
    def value(self) -> str:
        return self.model

    @property
    def label(self) -> str:
        return self.display_name

    @property
    def modalities(self) -> list[str]:
        return self.input_modalities

    @property
    def speeds(self) -> list[str]:
        return self.speed_tiers

    @property
    def reasoning_efforts(self) -> list[str]:
        return self.supported_reasoning_efforts

    @property
    def speed_options(self) -> list[str]:
        return self.speed_tiers

    @property
    def supports_image(self) -> bool:
        return "image" in self.input_modalities


@dataclass(slots=True)
class AiModelCatalog:
    provider_key: str
    models: list[AiModelDescriptor] = field(default_factory=list)
    fetched_at: str = ""
    cli_path: str = ""
    cli_version: str = ""
    source: str = "fallback"

    @classmethod
    def from_dict(cls, data: Any) -> "AiModelCatalog":
        if not isinstance(data, dict):
            raise ValueError("AI model catalog must be an object")

        provider_key = data.get("provider_key", data.get("providerKey", ""))
        fetched_at = data.get("fetched_at", data.get("fetchedAt", ""))
        cli_path = data.get("cli_path", data.get("cliPath", ""))
        cli_version = data.get("cli_version", data.get("cliVersion", ""))
        source = data.get("source", "fallback")
        if not all(isinstance(item, str) for item in (provider_key, fetched_at, cli_path, cli_version, source)):
            raise ValueError("AI model catalog contains an invalid string field")

        raw_models = data.get("models", [])
        if not isinstance(raw_models, list):
            raise ValueError("AI model catalog models must be a list")
        models = [AiModelDescriptor.from_dict(item) for item in raw_models]

        provider_key = provider_key.strip()
        source = source.strip()
        if provider_key not in KNOWN_AI_PROVIDERS:
            raise ValueError("AI model catalog contains an unknown provider")
        if source not in AI_MODEL_CATALOG_SOURCES:
            raise ValueError("AI model catalog contains an invalid source")

        return cls(
            provider_key=provider_key,
            models=models,
            fetched_at=fetched_at.strip(),
            cli_path=cli_path.strip(),
            cli_version=cli_version.strip(),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_key": self.provider_key,
            "models": [model.to_dict() for model in self.models],
            "fetched_at": self.fetched_at,
            "cli_path": self.cli_path,
            "cli_version": self.cli_version,
            "source": self.source,
        }

    @property
    def default_model(self) -> str:
        return next((model.model for model in self.models if model.is_default), "")


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
