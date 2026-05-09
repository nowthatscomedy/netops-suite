from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

AUTO_INCREMENT_NONE = "none"
AUTO_INCREMENT_SUFFIX_NUMBER = "suffix_number"
AUTO_INCREMENT_IPV4 = "ipv4"
AUTO_INCREMENT_MODES = (
    AUTO_INCREMENT_NONE,
    AUTO_INCREMENT_SUFFIX_NUMBER,
    AUTO_INCREMENT_IPV4,
)


@dataclass(slots=True)
class VariableSpec:
    name: str
    required: bool = False
    type: str = "string"
    default: Any = None
    description: str = ""
    description_ko: str = ""
    auto_increment: str = AUTO_INCREMENT_NONE


@dataclass(slots=True)
class BlockSpec:
    name: str
    lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Profile:
    id: str
    vendor: str
    model: str
    firmware: str
    description: str = ""
    description_ko: str = ""
    variables: dict[str, VariableSpec] = field(default_factory=dict)
    blocks: list[BlockSpec] = field(default_factory=list)
    source: str = ""


@dataclass(slots=True)
class DeviceRecord:
    row_number: int
    values: dict[str, Any]

    @property
    def device_id(self) -> str:
        return str(self.values.get("device_id", "")).strip()

    @property
    def profile_id(self) -> str:
        return str(self.values.get("profile_id", "")).strip()

    @property
    def display_name(self) -> str:
        for key in ("device_id", "hostname", "name"):
            value = str(self.values.get(key, "")).strip()
            if value:
                return value
        return f"row-{self.row_number}"


@dataclass(slots=True)
class ValidationIssue:
    level: str
    scope: str
    message: str
    source: str = ""
    profile_id: str = ""
    device_id: str = ""
    row_number: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "scope": self.scope,
            "message": self.message,
            "source": self.source,
            "profile_id": self.profile_id,
            "device_id": self.device_id,
            "row_number": self.row_number,
        }


@dataclass(slots=True)
class RenderedConfig:
    device_id: str
    profile_id: str
    text: str
    values: dict[str, Any]
    display_name: str = ""
