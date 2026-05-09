from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field


def _normalize_list(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_prefix(value: str | int | None) -> int:
    if value in (None, ""):
        return 24
    if isinstance(value, int):
        return max(1, min(32, value))
    text = str(value).strip()
    if text.isdigit():
        parsed = int(text)
        return max(1, min(32, parsed))
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen
    except (ipaddress.NetmaskValueError, ValueError):
        return 24


@dataclass(slots=True)
class IPProfile:
    name: str
    mode: str = "static"
    interface_name: str = ""
    local_ip: str = ""
    prefix: int = 24
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    target_vendor: str = ""
    target_ip: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "IPProfile":
        return cls(
            name=data.get("name", ""),
            mode=data.get("mode", "static"),
            interface_name=data.get("interface_name", ""),
            local_ip=data.get("local_ip", ""),
            prefix=_normalize_prefix(data.get("prefix", 24)),
            gateway=data.get("gateway", ""),
            dns=_normalize_list(data.get("dns")),
            target_vendor=data.get("target_vendor", "") or data.get("vendor", ""),
            target_ip=data.get("target_ip", "") or data.get("default_target_ip", ""),
            notes=data.get("notes", ""),
        )

    @classmethod
    def from_vendor_preset_dict(cls, data: dict) -> "IPProfile":
        return cls(
            name=data.get("name", ""),
            mode="static",
            interface_name="",
            local_ip=data.get("local_ip", ""),
            prefix=_normalize_prefix(data.get("prefix", 24)),
            gateway=data.get("gateway", ""),
            dns=_normalize_list(data.get("dns")),
            target_vendor=data.get("target_vendor", ""),
            target_ip=data.get("default_target_ip", ""),
            notes=data.get("notes", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "interface_name": self.interface_name,
            "local_ip": self.local_ip,
            "prefix": self.prefix,
            "gateway": self.gateway,
            "dns": self.dns,
            "target_vendor": self.target_vendor,
            "target_ip": self.target_ip,
            "notes": self.notes,
        }


@dataclass(slots=True)
class VendorPreset:
    name: str
    target_vendor: str = ""
    local_ip: str = ""
    prefix: int = 24
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    default_target_ip: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VendorPreset":
        return cls(
            name=data.get("name", ""),
            target_vendor=data.get("target_vendor", ""),
            local_ip=data.get("local_ip", ""),
            prefix=int(data.get("prefix", 24) or 24),
            gateway=data.get("gateway", ""),
            dns=_normalize_list(data.get("dns")),
            default_target_ip=data.get("default_target_ip", ""),
            notes=data.get("notes", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target_vendor": self.target_vendor,
            "local_ip": self.local_ip,
            "prefix": self.prefix,
            "gateway": self.gateway,
            "dns": self.dns,
            "default_target_ip": self.default_target_ip,
            "notes": self.notes,
        }


