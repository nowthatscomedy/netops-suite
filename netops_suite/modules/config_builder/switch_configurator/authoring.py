from __future__ import annotations

import csv
import ipaddress
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .engine import ConfigEngine
from .io_utils import SUPPORTED_PROFILE_EXTENSIONS, parse_profile_yaml
from .models import (
    AUTO_INCREMENT_MODES,
    AUTO_INCREMENT_NONE,
    DeviceRecord,
    Profile,
)
from .presenters import format_display_value


VARIABLE_TYPE_OPTIONS = ("string", "ipv4", "bool", "int")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HARDCODED_SECRET_PATTERNS = (
    re.compile(
        r"^\s*enable\s+(?:algorithm-type\s+\S+\s+)?secret\s+(?:\d+\s+)?"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*username\s+\S+.*?\s(?:password|secret)\s+(?:\d+\s+)?"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*snmp-server\s+community\s+"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:password|community)\s+"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:radius-server|tacacs-server)\b[^\r\n]*?\b(?:shared-)?key\b\s+"
        r"(?:(?:\d+|clear|cipher|encrypted|simple)\s+)*"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bkey-string\b\s+(?:(?:\d+|clear|cipher|encrypted|simple)\s+)*"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:pre-shared-key|wpa-psk)\b\s+"
        r"(?:(?:\d+|ascii|hex|clear|cipher|encrypted|simple|plain(?:text)?|pass-phrase|local|remote)\s+)*"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bauth\s+(?:md5|sha(?:-?(?:224|256|384|512))?)\s+"
        r"(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpriv\s+(?:des|3des|aes(?:-?(?:128|192|256))?)\s+"
        r"(?:\d+\s+)?(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}(?!\S)|\S+)",
        re.IGNORECASE,
    ),
)
SECRET_JINJA_VALUE_PATTERN = re.compile(
    r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}"
)
RISKY_COMMAND_PATTERNS = (
    re.compile(
        r"^\s*(?:erase|format|factory[- ]?reset|reload|reboot|restart|commit|save)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*write\s+(?:erase|memory)\b", re.IGNORECASE),
    re.compile(
        r"^\s*copy\s+(?:run|running-config)\s+(?:start|startup-config)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*no\s+(?:aaa|username|ip\s+ssh|ssh|line\s+vty|management|netconf|restconf)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*transport\s+input\s+none\b", re.IGNORECASE),
    re.compile(r"^\s*(?:shutdown|access-class|ip\s+access-group)\b", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class ProfileBuilderValidation:
    yaml_text: str
    profile: Profile | None
    issues: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    rendered_preview: str = ""

    @property
    def is_valid(self) -> bool:
        return not self.issues and self.profile is not None


@dataclass(frozen=True, slots=True)
class ProfileSaveTarget:
    path: Path
    exists: bool


class ProfileSaveConflictError(ValueError):
    """Raised when saving would overwrite a different profile or file."""


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
        if variable_type not in VARIABLE_TYPE_OPTIONS:
            issues.append(f"변수 {name}: 지원하지 않는 변수 타입입니다: {variable_type}")
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


def validate_profile_builder_state(state: dict[str, Any]) -> ProfileBuilderValidation:
    yaml_text, builder_issues = build_profile_yaml_from_state(state)
    if builder_issues:
        return ProfileBuilderValidation(
            yaml_text=yaml_text,
            profile=None,
            issues=tuple(builder_issues),
        )
    return validate_profile_yaml_for_save(yaml_text, source="<profile-builder>")


def validate_profile_yaml_for_save(
    yaml_text: str,
    *,
    source: str = "<profile-builder>",
) -> ProfileBuilderValidation:
    """Parse, validate and synthetically render a profile before persistence."""

    try:
        profile = parse_profile_yaml(yaml_text, source)
    except Exception as exc:
        return ProfileBuilderValidation(
            yaml_text=yaml_text,
            profile=None,
            issues=(f"프로파일 YAML을 읽을 수 없습니다: {exc}",),
        )

    issues: list[str] = []
    warnings: list[str] = []
    for label, value in (
        ("프로파일 ID", profile.id),
        ("벤더", profile.vendor),
        ("모델", profile.model),
        ("펌웨어", profile.firmware),
    ):
        if not value:
            issues.append(f"{label} 값이 비어 있습니다.")
    engine = ConfigEngine({profile.id: profile})
    issues.extend(
        issue.message
        for issue in engine.validate_profiles()
        if issue.level == "error"
    )

    for variable_name, variable in profile.variables.items():
        if (
            _looks_like_secret_variable_name(variable_name)
            and _has_nonempty_default(variable.default)
        ):
            issues.append(
                f"비밀 변수 {variable_name}에는 기본값을 직접 입력할 수 없습니다."
            )

    command_issues, command_warnings = _inspect_profile_commands(profile)
    issues.extend(command_issues)
    warnings.extend(command_warnings)

    rendered_preview = ""
    if not issues:
        sample_values = {
            "device_id": "profile-validation-preview",
            "profile_id": profile.id,
        }
        for variable_name, variable in profile.variables.items():
            sample_values[variable_name] = _synthetic_value_for_variable(variable.type, variable.default)
        sample_record = DeviceRecord(row_number=2, values=sample_values)
        device_issues = engine.validate_device_records([sample_record])
        issues.extend(
            issue.message for issue in device_issues if issue.level == "error"
        )
        if not issues:
            try:
                rendered_preview = engine.render_device(sample_record).text
            except Exception as exc:
                issues.append(f"합성 샘플 CLI를 생성할 수 없습니다: {exc}")

    return ProfileBuilderValidation(
        yaml_text=yaml_text,
        profile=profile,
        issues=tuple(_deduplicate_messages(issues)),
        warnings=tuple(_deduplicate_messages(warnings)),
        rendered_preview=rendered_preview,
    )


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


def inspect_profile_save_target(
    profile_id: str,
    directory: str | Path,
    *,
    source_path: str | Path | None = None,
) -> ProfileSaveTarget:
    """Resolve a safe target and detect case-insensitive ID/file collisions."""

    normalized_id = str(profile_id).strip().casefold()
    if not normalized_id:
        raise ProfileSaveConflictError("프로파일 ID가 비어 있어 저장 경로를 만들 수 없습니다.")

    target_directory = Path(directory).resolve()
    if target_directory.exists() and not target_directory.is_dir():
        raise ProfileSaveConflictError("프로파일 저장 위치가 폴더가 아닙니다.")
    source = _safe_profile_source_path(source_path, target_directory)
    if source is not None:
        target_path = source
    else:
        file_stem = normalize_identifier(profile_id) or "profile"
        target_path = target_directory / f"{file_stem}.yaml"

    candidates = (
        [
            path
            for path in target_directory.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_PROFILE_EXTENSIONS
        ]
        if target_directory.exists()
        else []
    )

    if source is None:
        target_name_match = next(
            (
                path
                for path in candidates
                if path.stem.casefold() == target_path.stem.casefold()
            ),
            None,
        )
        if target_name_match is not None:
            try:
                existing_profile = parse_profile_yaml(
                    target_name_match.read_text(encoding="utf-8"),
                    str(target_name_match),
                )
            except Exception as exc:
                raise ProfileSaveConflictError(
                    f"같은 파일명의 기존 프로파일을 확인할 수 없습니다: "
                    f"{target_name_match.name} ({exc})"
                ) from exc
            if existing_profile.id.casefold() != normalized_id:
                raise ProfileSaveConflictError(
                    f"대소문자를 구분하지 않을 때 같은 파일명이 이미 사용 중입니다: "
                    f"{target_name_match.name}"
                )
            target_path = target_name_match

    for path in candidates:
        if _same_resolved_path(path, target_path):
            continue
        if path.stem.casefold() == target_path.stem.casefold():
            raise ProfileSaveConflictError(
                f"대소문자를 구분하지 않을 때 같은 파일명이 이미 사용 중입니다: {path.name}"
            )
        try:
            existing_profile = parse_profile_yaml(path.read_text(encoding="utf-8"), str(path))
        except Exception:
            continue
        if existing_profile.id.casefold() == normalized_id:
            raise ProfileSaveConflictError(
                f"대소문자를 구분하지 않을 때 같은 프로파일 ID가 이미 사용 중입니다: "
                f"{existing_profile.id} ({path.name})"
            )

    return ProfileSaveTarget(path=target_path, exists=target_path.exists())


def save_profile_yaml_to_directory(
    profile_id: str,
    yaml_text: str,
    directory: str | Path,
    *,
    source_path: str | Path | None = None,
    allow_overwrite: bool = False,
) -> tuple[Path, bool]:
    """Validate and atomically save a profile, retaining one recovery backup."""

    validation = validate_profile_yaml_for_save(yaml_text, source="<profile-save>")
    if validation.issues:
        raise ValueError("; ".join(validation.issues))
    if validation.profile is None or validation.profile.id.casefold() != str(profile_id).strip().casefold():
        raise ValueError("저장 요청의 프로파일 ID와 YAML의 프로파일 ID가 일치하지 않습니다.")

    target = inspect_profile_save_target(profile_id, directory, source_path=source_path)
    if target.exists and not allow_overwrite:
        raise FileExistsError(f"기존 프로파일을 덮어쓰려면 확인이 필요합니다: {target.path.name}")

    target.path.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists
    backup_path = target.path.with_suffix(f"{target.path.suffix}.bak")
    temporary_path: Path | None = None
    backup_temporary_path: Path | None = None
    backup_created = False
    target_replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.path.parent,
            prefix=f".{target.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(yaml_text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)

        if existed:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.path.parent,
                prefix=f".{backup_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as backup_temporary:
                backup_temporary_path = Path(backup_temporary.name)
            shutil.copy2(target.path, backup_temporary_path)
            os.replace(backup_temporary_path, backup_path)
            backup_temporary_path = None
            backup_created = True

        os.replace(temporary_path, target.path)
        temporary_path = None
        target_replaced = True

        saved_text = target.path.read_text(encoding="utf-8")
        if saved_text != yaml_text:
            raise OSError("저장 후 다시 읽은 프로파일 내용이 요청한 내용과 다릅니다.")
        saved_validation = validate_profile_yaml_for_save(saved_text, source=str(target.path))
        if saved_validation.issues or saved_validation.profile is None:
            raise OSError(
                "저장 후 프로파일 검증에 실패했습니다: "
                + "; ".join(saved_validation.issues or ("프로파일을 다시 읽을 수 없습니다.",))
            )
        if saved_validation.profile.id.casefold() != str(profile_id).strip().casefold():
            raise OSError("저장 후 다시 읽은 프로파일 ID가 요청한 ID와 다릅니다.")
    except Exception:
        if target_replaced and existed and backup_created and backup_path.exists():
            os.replace(backup_path, target.path)
        elif target_replaced and not existed and target.path.exists():
            target.path.unlink()
        raise
    finally:
        for temporary in (temporary_path, backup_temporary_path):
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    return target.path, existed


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


def _safe_profile_source_path(
    source_path: str | Path | None,
    target_directory: Path,
) -> Path | None:
    if source_path is None or not str(source_path).strip():
        return None
    source = Path(source_path).resolve()
    if source.parent != target_directory:
        raise ProfileSaveConflictError("프로파일 폴더 밖의 원본 파일에는 저장할 수 없습니다.")
    if source.suffix.lower() not in SUPPORTED_PROFILE_EXTENSIONS:
        raise ProfileSaveConflictError("원본 프로파일 파일 확장자가 YAML 형식이 아닙니다.")
    return source


def _same_resolved_path(left: Path, right: Path) -> bool:
    return str(left.resolve()).casefold() == str(right.resolve()).casefold()


def _synthetic_value_for_variable(variable_type: str, default: Any) -> Any:
    if default is not None:
        return default
    return {
        "string": "sample-value",
        "ipv4": "192.0.2.10",
        "bool": True,
        "int": 1,
    }.get(variable_type, "sample-value")


def _inspect_profile_commands(profile: Profile) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    for block in profile.blocks:
        for line_number, line in enumerate(block.lines, start=1):
            command_text = re.sub(r"{%.*?%}", " ", line).strip()
            for pattern in HARDCODED_SECRET_PATTERNS:
                match = pattern.search(command_text)
                if match and not _is_jinja_secret_value(match.group(1)):
                    issues.append(
                        f"비밀값을 명령어에 직접 입력할 수 없습니다 "
                        f"({block.name} {line_number}행). 변수를 사용하세요."
                    )
                    break
            if any(pattern.search(command_text) for pattern in RISKY_COMMAND_PATTERNS):
                warnings.append(
                    f"장비 상태 또는 관리 접속에 영향을 줄 수 있는 명령이 있습니다 "
                    f"({block.name} {line_number}행): {line.strip()}"
                )
    return _deduplicate_messages(issues), _deduplicate_messages(warnings)


def _is_jinja_secret_value(value: str) -> bool:
    return bool(SECRET_JINJA_VALUE_PATTERN.fullmatch(str(value).strip()))


def _has_nonempty_default(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _looks_like_secret_variable_name(name: str) -> bool:
    split_acronyms = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", split_acronyms)
    tokens = set(re.findall(r"[a-z0-9]+", split_camel.casefold()))
    if tokens & {
        "community",
        "credential",
        "pass",
        "passcode",
        "passphrase",
        "passwd",
        "password",
        "psk",
        "pwd",
        "secret",
        "token",
    }:
        return True
    return "key" in tokens and bool(
        tokens
        & {
            "access",
            "api",
            "auth",
            "private",
            "priv",
            "radius",
            "shared",
            "tacacs",
            "wpa",
        }
    )


def _deduplicate_messages(messages: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for message in messages:
        normalized = str(message).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


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
