from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

import yaml

from core.i18n import DEFAULT_LANGUAGE, normalize_language_code
from core.path_utils import get_app_dir

REQUIRED_INPUT_COLUMNS: tuple[str, ...] = (
    "ip",
    "vendor",
    "os",
    "connection_type",
    "port",
    "password",
)
OPTIONAL_INPUT_COLUMNS: tuple[str, ...] = ("username", "enable_password")
VALID_INPUT_COLUMNS: set[str] = set(REQUIRED_INPUT_COLUMNS + OPTIONAL_INPUT_COLUMNS)

_DEFAULT_INPUT_COLUMN_ALIASES: Dict[str, str] = {
    "ip address": "ip",
    "vendor name": "vendor",
    "connection type": "connection_type",
    "enable password": "enable_password",
    "user name": "username",
}


def _normalize_column_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().lower().split())


def _normalize_input_column_target(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "_".join(value.strip().lower().replace("-", " ").split())


def canonicalize_column_name(column: object, aliases: Dict[str, str] | None = None) -> str:
    cleaned = str(column).strip() if isinstance(column, str) else ""
    if not cleaned:
        return ""
    if not aliases:
        return cleaned
    return aliases.get(_normalize_column_key(cleaned), cleaned)


def canonicalize_input_column_name(
    column: object,
    aliases: Dict[str, str] | None = None,
) -> str:
    cleaned = str(column).strip() if isinstance(column, str) else ""
    if not cleaned:
        return ""

    normalized_aliases: Dict[str, str] = {}
    normalized_aliases.update(_DEFAULT_INPUT_COLUMN_ALIASES)
    if aliases:
        normalized_aliases.update(aliases)

    alias_key = _normalize_column_key(cleaned)
    mapped = normalized_aliases.get(alias_key)
    if mapped:
        return mapped

    candidate = _normalize_input_column_target(cleaned)
    if candidate in VALID_INPUT_COLUMNS:
        return candidate
    return candidate


def _normalize_profile_part(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def make_profile_key(vendor: object, os_name: object) -> str:
    vendor_key = _normalize_profile_part(vendor)
    os_key = _normalize_profile_part(os_name)
    if not vendor_key or not os_key:
        return ""
    return f"{vendor_key}|{os_key}"


def normalize_profile_key(profile: object) -> str:
    if not isinstance(profile, str):
        return ""
    parts = profile.split("|", 1)
    if len(parts) != 2:
        return ""
    return make_profile_key(parts[0], parts[1])


def _normalize_column_aliases(raw: object) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, str] = {}
    for alias_raw, canonical_raw in raw.items():
        alias_key = _normalize_column_key(alias_raw)
        canonical = str(canonical_raw).strip() if isinstance(canonical_raw, str) else ""
        if not alias_key or not canonical:
            continue

        normalized[alias_key] = canonical
        canonical_key = _normalize_column_key(canonical)
        if canonical_key:
            normalized[canonical_key] = canonical

    return normalized


def _normalize_input_column_aliases(raw: object) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, str] = {}
    for alias_raw, canonical_raw in raw.items():
        alias_key = _normalize_column_key(alias_raw)
        canonical = _normalize_input_column_target(canonical_raw)
        if not alias_key or canonical not in VALID_INPUT_COLUMNS:
            continue
        normalized[alias_key] = canonical
    return normalized


def _normalize_column_order(raw: object, aliases: Dict[str, str]) -> List[str]:
    if not isinstance(raw, list):
        return []

    ordered: List[str] = []
    seen: set[str] = set()
    for column in raw:
        canonical = canonicalize_column_name(column, aliases)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(canonical)
    return ordered


def _normalize_profile_orders(raw: object, aliases: Dict[str, str]) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, List[str]] = {}
    for profile_raw, columns_raw in raw.items():
        profile_key = normalize_profile_key(profile_raw)
        if not profile_key:
            continue
        columns = _normalize_column_order(columns_raw, aliases)
        if columns:
            normalized[profile_key] = columns

    return normalized


@dataclass
class AppSettings:
    console_log_level: str = "WARNING"
    inspection_excludes: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)
    max_retries: int = 3
    timeout: int = 10
    max_workers: int = 10
    language: str = DEFAULT_LANGUAGE
    fallback_language: str = DEFAULT_LANGUAGE
    input_column_aliases: Dict[str, str] = field(default_factory=dict)
    column_aliases: Dict[str, str] = field(default_factory=dict)
    inspection_column_order_global: List[str] = field(default_factory=list)
    inspection_column_order_by_profile: Dict[str, List[str]] = field(default_factory=dict)


def get_settings_path() -> Path:
    return get_app_dir() / "settings.yaml"


def _normalize_excludes(raw: object) -> Dict[str, Dict[str, List[str]]]:
    if not isinstance(raw, dict):
        return {}

    normalized: Dict[str, Dict[str, List[str]]] = {}
    for vendor_key, os_map in raw.items():
        if not isinstance(vendor_key, str) or not isinstance(os_map, dict):
            continue
        vendor = vendor_key.strip().lower()
        if not vendor:
            continue

        normalized_os: Dict[str, List[str]] = {}
        for os_key, commands in os_map.items():
            if not isinstance(os_key, str) or not isinstance(commands, list):
                continue
            os_name = os_key.strip().lower()
            if not os_name:
                continue

            cleaned_commands = []
            for cmd in commands:
                if isinstance(cmd, str) and cmd.strip():
                    cleaned_commands.append(cmd.strip())
            if cleaned_commands:
                normalized_os[os_name] = cleaned_commands

        if normalized_os:
            normalized[vendor] = normalized_os

    return normalized


def _load_settings_data(settings_path: Path) -> dict | None:
    """Read settings data with YAML first and JSON fallback."""
    if settings_path.exists():
        raw = settings_path.read_text(encoding="utf-8")
        if settings_path.suffix in (".yaml", ".yml"):
            return yaml.safe_load(raw)
        return json.loads(raw)

    json_fallback = settings_path.with_suffix(".json")
    if json_fallback.exists():
        return json.loads(json_fallback.read_text(encoding="utf-8"))

    return None


def load_settings() -> AppSettings:
    settings_path = get_settings_path()

    try:
        data = _load_settings_data(settings_path)
    except Exception:
        return AppSettings()

    if data is None or not isinstance(data, dict):
        return AppSettings()

    console_log_level = data.get("console_log_level", "WARNING")
    if not isinstance(console_log_level, str) or not console_log_level:
        console_log_level = "WARNING"

    inspection_excludes = _normalize_excludes(data.get("inspection_excludes", {}))

    max_retries = data.get("max_retries", 3)
    if not isinstance(max_retries, int) or max_retries < 1:
        max_retries = 3

    timeout = data.get("timeout", 10)
    if not isinstance(timeout, int) or timeout < 1:
        timeout = 10

    max_workers = data.get("max_workers", 10)
    if not isinstance(max_workers, int) or max_workers < 1:
        max_workers = 10

    language = normalize_language_code(data.get("language"), DEFAULT_LANGUAGE)
    fallback_language = normalize_language_code(
        data.get("fallback_language"),
        DEFAULT_LANGUAGE,
    )

    input_column_aliases = _normalize_input_column_aliases(
        data.get("input_column_aliases", {}),
    )
    column_aliases = _normalize_column_aliases(data.get("column_aliases", {}))
    inspection_column_order_global = _normalize_column_order(
        data.get("inspection_column_order_global", []),
        column_aliases,
    )
    inspection_column_order_by_profile = _normalize_profile_orders(
        data.get("inspection_column_order_by_profile", {}),
        column_aliases,
    )

    return AppSettings(
        console_log_level=console_log_level.upper(),
        inspection_excludes=inspection_excludes,
        max_retries=max_retries,
        timeout=timeout,
        max_workers=max_workers,
        language=language,
        fallback_language=fallback_language,
        input_column_aliases=input_column_aliases,
        column_aliases=column_aliases,
        inspection_column_order_global=inspection_column_order_global,
        inspection_column_order_by_profile=inspection_column_order_by_profile,
    )


def resolve_inspection_column_order(
    available_columns: Sequence[str],
    device_profiles: Sequence[str],
    settings: AppSettings,
) -> List[str]:
    aliases = settings.column_aliases
    available = _normalize_column_order(list(available_columns), aliases)
    if not available:
        return []

    available_set = set(available)
    merged: List[str] = []

    def append(columns: Sequence[str]) -> None:
        for column in columns:
            canonical = canonicalize_column_name(column, aliases)
            if not canonical:
                continue
            if canonical not in available_set or canonical in merged:
                continue
            merged.append(canonical)

    append(settings.inspection_column_order_global)

    seen_profiles: set[str] = set()
    for profile in device_profiles:
        profile_key = normalize_profile_key(profile)
        if not profile_key or profile_key in seen_profiles:
            continue
        seen_profiles.add(profile_key)
        append(settings.inspection_column_order_by_profile.get(profile_key, []))

    append(available)
    return merged


def save_settings(settings: AppSettings) -> None:
    aliases = _normalize_column_aliases(settings.column_aliases)
    input_aliases = _normalize_input_column_aliases(settings.input_column_aliases)
    global_order = _normalize_column_order(settings.inspection_column_order_global, aliases)
    profile_orders = _normalize_profile_orders(settings.inspection_column_order_by_profile, aliases)

    normalized_settings = AppSettings(
        console_log_level=str(settings.console_log_level).upper() or "WARNING",
        inspection_excludes=_normalize_excludes(settings.inspection_excludes),
        max_retries=settings.max_retries
        if isinstance(settings.max_retries, int) and settings.max_retries > 0
        else 3,
        timeout=settings.timeout if isinstance(settings.timeout, int) and settings.timeout > 0 else 10,
        max_workers=settings.max_workers
        if isinstance(settings.max_workers, int) and settings.max_workers > 0
        else 10,
        language=normalize_language_code(settings.language, DEFAULT_LANGUAGE),
        fallback_language=normalize_language_code(settings.fallback_language, DEFAULT_LANGUAGE),
        input_column_aliases=input_aliases,
        column_aliases=aliases,
        inspection_column_order_global=global_order,
        inspection_column_order_by_profile=profile_orders,
    )

    settings_path = get_settings_path()
    settings_path.write_text(
        yaml.dump(
            asdict(normalized_settings),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
