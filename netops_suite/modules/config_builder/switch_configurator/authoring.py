from __future__ import annotations

import csv
import ipaddress
import re
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .models import (
    AUTO_INCREMENT_MODES,
    AUTO_INCREMENT_NONE,
    DeviceRecord,
    Profile,
)
from .presenters import format_display_value


VARIABLE_TYPE_OPTIONS = ("string", "ipv4", "bool", "int")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def make_empty_profile_builder_state() -> dict[str, Any]:
    return {
        "id": "",
        "vendor": "",
        "model": "",
        "firmware": "",
        "description": "",
        "variables": [make_empty_variable_row()],
        "blocks": [make_empty_block_row()],
    }


def make_empty_variable_row() -> dict[str, Any]:
    return {
        "_row_id": uuid4().hex,
        "name": "",
        "required": False,
        "type": "string",
        "default_input": "",
        "description": "",
        "auto_increment": AUTO_INCREMENT_NONE,
    }


def make_empty_block_row() -> dict[str, str]:
    return {
        "_row_id": uuid4().hex,
        "name": "",
        "lines_text": "",
    }


def profile_to_builder_state(profile: Profile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "vendor": profile.vendor,
        "model": profile.model,
        "firmware": profile.firmware,
        "description": profile.description_ko or profile.description,
        "variables": [
            {
                "_row_id": uuid4().hex,
                "name": variable.name,
                "required": variable.required,
                "type": variable.type,
                "default_input": format_display_value(variable.default),
                "description": variable.description_ko or variable.description,
                "auto_increment": variable.auto_increment or AUTO_INCREMENT_NONE,
            }
            for variable in profile.variables.values()
        ]
        or [make_empty_variable_row()],
        "blocks": [
            {
                "_row_id": uuid4().hex,
                "name": block.name,
                "lines_text": "\n".join(block.lines),
            }
            for block in profile.blocks
        ]
        or [make_empty_block_row()],
    }


def build_profile_yaml_from_state(state: dict[str, Any]) -> tuple[str, list[str]]:
    issues: list[str] = []

    profile_id = str(state.get("id", "")).strip()
    vendor = str(state.get("vendor", "")).strip()
    model = str(state.get("model", "")).strip()
    firmware = str(state.get("firmware", "")).strip()
    description = str(state.get("description", "")).strip()

    if not profile_id:
        issues.append("프로파일 ID를 입력하세요.")
    if not vendor:
        issues.append("벤더를 입력하세요.")
    if not model:
        issues.append("모델을 입력하세요.")
    if not firmware:
        issues.append("펌웨어 버전을 입력하세요.")

    variables: dict[str, dict[str, Any]] = {}
    seen_variable_names: set[str] = set()
    for index, raw_row in enumerate(state.get("variables", []), start=1):
        row = dict(raw_row)
        if _is_blank_variable_row(row):
            continue

        name = str(row.get("name", "")).strip()
        required = bool(row.get("required", False))
        variable_type = str(row.get("type", "string")).strip().lower() or "string"
        default_input = str(row.get("default_input", "")).strip()
        description_text = str(row.get("description", "")).strip()
        auto_increment = str(row.get("auto_increment", AUTO_INCREMENT_NONE)).strip().lower() or AUTO_INCREMENT_NONE

        if not name:
            issues.append(f"변수 {index}: 이름이 비어 있습니다.")
            continue
        if name in seen_variable_names:
            issues.append(f"변수 이름이 중복되었습니다: {name}")
            continue
        if not is_valid_identifier(name):
            normalized_name = normalize_identifier(name)
            example_name = normalized_name or "enable_password"
            issues.append(
                f"변수 {name}: 변수명은 공백 없이 영문/숫자/언더바(_)만 사용할 수 있습니다. 예: {example_name}"
            )
            continue
        if auto_increment not in AUTO_INCREMENT_MODES:
            issues.append(f"변수 {name}: 연속 값 규칙은 none, suffix_number, ipv4 중 하나여야 합니다.")
            continue
        seen_variable_names.add(name)

        variable_doc: dict[str, Any] = {
            "required": required,
            "type": variable_type,
        }

        default_value, default_issue = _coerce_builder_default(default_input, variable_type)
        if default_issue:
            issues.append(f"변수 {name}: {default_issue}")
        if default_value is not None:
            variable_doc["default"] = default_value
        if description_text:
            variable_doc["description"] = description_text
        if auto_increment != AUTO_INCREMENT_NONE:
            variable_doc["auto_increment"] = auto_increment

        variables[name] = variable_doc

    blocks: list[dict[str, Any]] = []
    for index, raw_row in enumerate(state.get("blocks", []), start=1):
        row = dict(raw_row)
        if _is_blank_block_row(row):
            continue

        name = str(row.get("name", "")).strip()
        lines = [line.rstrip() for line in str(row.get("lines_text", "")).splitlines() if line.strip()]

        if not name:
            issues.append(f"블록 {index}: 이름이 비어 있습니다.")
            continue
        if not lines:
            issues.append(f"블록 {name}: 명령어를 한 줄 이상 입력하세요.")
            continue

        block_doc: dict[str, Any] = {
            "name": name,
            "lines": lines,
        }
        blocks.append(block_doc)

    if not variables:
        issues.append("변수를 한 개 이상 입력하세요.")
    if not blocks:
        issues.append("명령 블록을 한 개 이상 입력하세요.")

    document: dict[str, Any] = {
        "id": profile_id,
        "vendor": vendor,
        "model": model,
        "firmware": firmware,
        "variables": variables,
        "blocks": blocks,
    }
    if description:
        document["description"] = description

    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True), issues


def make_empty_device_row(
    profile: Profile | None = None,
    profile_id: str = "",
) -> dict[str, str]:
    row = {
        "_row_id": uuid4().hex,
        "profile_id": profile.id if profile else str(profile_id).strip(),
    }
    if profile:
        for variable_name in profile.variables:
            row[variable_name] = ""
    return row


def align_device_rows_to_profile(profile: Profile, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return align_device_rows(rows, {profile.id: profile}, profile.id)


def align_device_rows(
    rows: list[dict[str, Any]],
    profiles: dict[str, Profile],
    default_profile_id: str = "",
) -> list[dict[str, str]]:
    aligned_rows: list[dict[str, str]] = []
    default_profile = _find_profile(profiles, default_profile_id)
    if not default_profile and profiles:
        default_profile = next(iter(profiles.values()))

    for raw_row in rows:
        requested_profile = _find_profile(profiles, str(raw_row.get("profile_id", "")).strip())
        profile = requested_profile or default_profile
        requested_profile_id = str(raw_row.get("profile_id", "")).strip()
        empty_row = make_empty_device_row(
            profile=profile,
            profile_id=profile.id if profile else requested_profile_id,
        )
        if raw_row.get("_row_id"):
            empty_row["_row_id"] = str(raw_row["_row_id"])
        for key in empty_row:
            if key in raw_row and raw_row[key] is not None:
                empty_row[key] = str(raw_row[key]).strip()
        for key, value in raw_row.items():
            normalized_key = str(key).strip()
            if (
                not normalized_key
                or normalized_key.startswith("_")
                or normalized_key in empty_row
                or value is None
            ):
                continue
            empty_row[normalized_key] = str(value).strip()
        if profile:
            empty_row["profile_id"] = profile.id
        elif requested_profile_id:
            empty_row["profile_id"] = requested_profile_id
        aligned_rows.append(empty_row)

    if aligned_rows:
        return aligned_rows

    if default_profile:
        return [make_empty_device_row(default_profile)]

    return [make_empty_device_row(profile_id=default_profile_id)]


def build_device_records_from_rows(
    profile_or_profiles: Profile | dict[str, Profile],
    rows: list[dict[str, Any]],
) -> list[DeviceRecord]:
    profiles, default_profile_id = _coerce_profile_map(profile_or_profiles)
    aligned_rows = align_device_rows(rows, profiles, default_profile_id)
    records: list[DeviceRecord] = []
    for row_number, row in enumerate(aligned_rows, start=2):
        if _is_blank_device_row(row):
            continue
        values = {key: value for key, value in row.items() if not key.startswith("_")}
        records.append(DeviceRecord(row_number=row_number, values=values))
    return records


def build_device_rows_from_records(
    profile_or_profiles: Profile | dict[str, Profile],
    records: list[DeviceRecord],
) -> list[dict[str, str]]:
    profiles, default_profile_id = _coerce_profile_map(profile_or_profiles)
    rows: list[dict[str, str]] = []
    for record in records:
        requested_profile_id = str(record.values.get("profile_id", "")).strip()
        requested_profile = _find_profile(profiles, requested_profile_id)
        row = make_empty_device_row(
            profile=requested_profile,
            profile_id=requested_profile_id,
        )
        for key, value in record.values.items():
            normalized_key = str(key).strip()
            if not normalized_key or normalized_key.startswith("_"):
                continue
            row[normalized_key] = "" if value is None else str(value).strip()
        rows.append(row)

    return align_device_rows(rows, profiles, default_profile_id)


def build_device_csv_preview(
    profile_or_profiles: Profile | dict[str, Profile],
    rows: list[dict[str, Any]],
) -> str:
    profiles, default_profile_id = _coerce_profile_map(profile_or_profiles)
    aligned_rows = align_device_rows(rows, profiles, default_profile_id)
    fieldnames = _collect_device_fieldnames(aligned_rows, profiles, default_profile_id)
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for record in build_device_records_from_rows(profiles, aligned_rows):
        writer.writerow({field: record.values.get(field, "") for field in fieldnames})
    return buffer.getvalue()


def apply_bulk_edit_to_rows(
    rows: list[dict[str, Any]],
    target_indexes: list[int],
    field_name: str,
    operation: str,
    *,
    value: str = "",
    replace_from: str = "",
    replace_to: str = "",
    sequence_start: str = "",
    sequence_step: int = 1,
) -> list[dict[str, str]]:
    normalized_field = str(field_name).strip()
    if not normalized_field:
        return [dict(row) for row in rows]

    updated_rows = [
        {str(key): "" if value is None else str(value).strip() for key, value in row.items()}
        for row in rows
    ]
    normalized_indexes = sorted(
        {
            index
            for index in target_indexes
            if 0 <= index < len(updated_rows)
        }
    )
    if not normalized_indexes:
        return updated_rows

    if operation == "set":
        for index in normalized_indexes:
            updated_rows[index][normalized_field] = str(value).strip()
        return updated_rows

    if operation == "prefix":
        prefix = str(value)
        for index in normalized_indexes:
            updated_rows[index][normalized_field] = prefix + updated_rows[index].get(normalized_field, "")
        return updated_rows

    if operation == "suffix":
        suffix = str(value)
        for index in normalized_indexes:
            updated_rows[index][normalized_field] = updated_rows[index].get(normalized_field, "") + suffix
        return updated_rows

    if operation == "replace":
        needle = str(replace_from)
        replacement = str(replace_to)
        if not needle:
            return updated_rows
        for index in normalized_indexes:
            updated_rows[index][normalized_field] = updated_rows[index].get(normalized_field, "").replace(
                needle,
                replacement,
            )
        return updated_rows

    if operation == "ipv4_sequence":
        start_ip = ipaddress.IPv4Address(str(sequence_start).strip())
        step = max(1, int(sequence_step))
        for offset, index in enumerate(normalized_indexes):
            updated_rows[index][normalized_field] = str(start_ip + (offset * step))
        return updated_rows

    raise ValueError(f"지원하지 않는 일괄 편집 작업입니다: {operation}")


def normalize_identifier(value: str) -> str:
    normalized = re.sub(r"\s+", "_", str(value).strip())
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if normalized and normalized[0].isdigit():
        normalized = f"_{normalized}"
    return normalized


def is_valid_identifier(value: str) -> bool:
    return bool(IDENTIFIER_PATTERN.fullmatch(str(value).strip()))


def save_profile_yaml_to_directory(
    profile_id: str,
    yaml_text: str,
    directory: str | Path,
) -> tuple[Path, bool]:
    target_directory = Path(directory)
    target_directory.mkdir(parents=True, exist_ok=True)

    file_stem = normalize_identifier(profile_id) or "profile"
    target_path = target_directory / f"{file_stem}.yaml"
    existed = target_path.exists()
    target_path.write_text(yaml_text, encoding="utf-8")
    return target_path, existed


def save_device_csv_to_directory(
    csv_text: str,
    directory: str | Path,
    file_name: str | None = None,
) -> tuple[Path, bool]:
    target_directory = Path(directory)
    target_directory.mkdir(parents=True, exist_ok=True)

    if file_name:
        requested_path = Path(file_name)
        file_stem = normalize_identifier(requested_path.stem) or "device_values"
        suffix = requested_path.suffix or ".csv"
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_stem = f"device_values_{timestamp}"
        suffix = ".csv"

    target_path = target_directory / f"{file_stem}{suffix}"
    sequence = 1
    while target_path.exists():
        target_path = target_directory / f"{file_stem}_{sequence}{suffix}"
        sequence += 1

    existed = False
    target_path.write_text(csv_text, encoding="utf-8")
    return target_path, existed


def _coerce_builder_default(value: str, variable_type: str) -> tuple[Any, str | None]:
    normalized = str(value).strip()
    if not normalized:
        return None, None

    if variable_type == "bool":
        lowered = normalized.lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True, None
        if lowered in {"false", "0", "no", "n", "off"}:
            return False, None
        return normalized, "bool 기본값은 true 또는 false로 입력하세요."

    if variable_type == "int":
        try:
            return int(normalized), None
        except ValueError:
            return normalized, "int 기본값은 숫자로 입력하세요."

    return normalized, None


def _is_blank_variable_row(row: dict[str, Any]) -> bool:
    return not any(
        [
            str(row.get("name", "")).strip(),
            str(row.get("default_input", "")).strip(),
            str(row.get("description", "")).strip(),
            bool(row.get("required", False)),
        ]
    )


def _is_blank_block_row(row: dict[str, Any]) -> bool:
    return not any(
        [
            str(row.get("name", "")).strip(),
            str(row.get("lines_text", "")).strip(),
        ]
    )


def _is_blank_device_row(row: dict[str, str]) -> bool:
    for key, value in row.items():
        if key == "profile_id" or key.startswith("_"):
            continue
        if str(value).strip():
            return False
    return True


def _coerce_profile_map(
    profile_or_profiles: Profile | dict[str, Profile],
) -> tuple[dict[str, Profile], str]:
    if isinstance(profile_or_profiles, Profile):
        return {profile_or_profiles.id: profile_or_profiles}, profile_or_profiles.id

    profiles = dict(profile_or_profiles)
    default_profile_id = next(iter(profiles), "")
    return profiles, default_profile_id


def _collect_device_fieldnames(
    rows: list[dict[str, str]],
    profiles: dict[str, Profile],
    default_profile_id: str,
) -> list[str]:
    fieldnames = ["profile_id"]
    seen = set(fieldnames)

    for row in rows:
        profile = _find_profile(profiles, row.get("profile_id", ""))
        if profile:
            for variable_name in profile.variables:
                if variable_name not in seen:
                    fieldnames.append(variable_name)
                    seen.add(variable_name)

        for key in row:
            if key.startswith("_") or key in seen:
                continue
            fieldnames.append(key)
            seen.add(key)

    if len(fieldnames) == 1:
        default_profile = _find_profile(profiles, default_profile_id)
        if default_profile:
            for variable_name in default_profile.variables:
                if variable_name not in seen:
                    fieldnames.append(variable_name)
                    seen.add(variable_name)

    return fieldnames


def _find_profile(profiles: dict[str, Profile], profile_id: str) -> Profile | None:
    normalized = str(profile_id).strip().casefold()
    if not normalized:
        return None

    for key, profile in profiles.items():
        if key.casefold() == normalized or profile.id.casefold() == normalized:
            return profile

    return None
