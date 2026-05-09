from __future__ import annotations

import json

from .models import Profile


def format_display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def pick_display_text(preferred: str, fallback: str) -> str:
    preferred_text = str(preferred or "").strip()
    if preferred_text:
        return preferred_text
    return str(fallback or "").strip()


def build_profile_variable_rows(profile: Profile) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for variable in profile.variables.values():
        rows.append(
            {
                "name": variable.name,
                "type": variable.type,
                "required": "required" if variable.required else "optional",
                "default": format_display_value(variable.default),
                "description": pick_display_text(variable.description_ko, variable.description),
            }
        )
    return rows
