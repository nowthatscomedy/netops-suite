from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from vendors import (
    BACKUP_COMMANDS,
    CONNECTION_OVERRIDES,
    HANDLER_OVERRIDES,
    INSPECTION_COMMANDS,
    MODEL_PROFILES,
    PARSING_RULES,
)


def normalize_profile_part(value: object) -> str:
    """Normalize a profile key without weakening exact model matching."""

    if value is None:
        return ""
    try:
        if value != value:  # NaN from optional spreadsheet cells.
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return ""
    return " ".join(text.casefold().split())


@dataclass(frozen=True, slots=True)
class ResolvedDeviceProfile:
    vendor: str
    os_name: str
    model: str
    inspection_commands: tuple[str, ...]
    backup_command: str
    parsing_rules: dict[str, Any]
    connection_overrides: dict[str, Any]
    handler_overrides: dict[str, Any]
    output_columns: tuple[str, ...] = ()
    os_version: str = ""
    model_requested: bool = False
    model_matched: bool = False
    model_handler_overridden: bool = False


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _model_profile(vendor: str, os_name: str, model: str) -> dict[str, Any] | None:
    value = MODEL_PROFILES.get(vendor, {}).get(os_name, {}).get(model)
    return value if isinstance(value, dict) else None


def has_model_profiles(vendor: object, os_name: object) -> bool:
    vendor_key = normalize_profile_part(vendor)
    os_key = normalize_profile_part(os_name)
    profiles = MODEL_PROFILES.get(vendor_key, {}).get(os_key, {})
    return isinstance(profiles, dict) and bool(profiles)


def resolve_device_profile(
    vendor: object,
    os_name: object,
    model: object = "",
) -> ResolvedDeviceProfile:
    """Resolve one complete runtime profile for both connection implementations.

    A matching model profile completely replaces commands, backup, and parsing.
    Connection and handler maps are the only sections inherited from the base
    vendor/OS profile, with model values winning key-by-key.
    """

    vendor_key = normalize_profile_part(vendor)
    os_key = normalize_profile_part(os_name)
    model_key = normalize_profile_part(model)

    base_commands = tuple(INSPECTION_COMMANDS.get(vendor_key, {}).get(os_key, ()))
    base_backup = str(BACKUP_COMMANDS.get(vendor_key, {}).get(os_key, "") or "")
    base_parsing = _mapping(PARSING_RULES.get(vendor_key, {}).get(os_key, {}))
    base_connection = _mapping(
        CONNECTION_OVERRIDES.get(vendor_key, {}).get(os_key, {})
    )
    base_handler = _mapping(HANDLER_OVERRIDES.get(vendor_key, {}).get(os_key, {}))

    selected = _model_profile(vendor_key, os_key, model_key) if model_key else None
    if selected is None:
        return ResolvedDeviceProfile(
            vendor=vendor_key,
            os_name=os_key,
            model=model_key,
            inspection_commands=base_commands,
            backup_command=base_backup,
            parsing_rules=base_parsing,
            connection_overrides=base_connection,
            handler_overrides=base_handler,
            model_requested=bool(model_key),
            model_matched=False,
        )

    connection = dict(base_connection)
    connection.update(_mapping(selected.get("connection_overrides")))
    selected_handler = _mapping(selected.get("handler_overrides"))
    handler = dict(base_handler)
    handler.update(selected_handler)
    output_columns = tuple(
        str(column).strip()
        for column in selected.get("output_columns", [])
        if str(column).strip()
    )
    return ResolvedDeviceProfile(
        vendor=vendor_key,
        os_name=os_key,
        model=model_key,
        inspection_commands=tuple(selected.get("inspection_commands", ())),
        backup_command=str(selected.get("backup_command", "") or ""),
        parsing_rules=_mapping(selected.get("parsing_rules")),
        connection_overrides=connection,
        handler_overrides=handler,
        output_columns=output_columns,
        os_version=str(selected.get("os_version", "") or "").strip(),
        model_requested=True,
        model_matched=True,
        model_handler_overridden=bool(selected_handler),
    )


__all__ = [
    "ResolvedDeviceProfile",
    "has_model_profiles",
    "normalize_profile_part",
    "resolve_device_profile",
]
