from __future__ import annotations

import ipaddress
import re
from typing import Any, Iterable

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, meta

from .models import DeviceRecord, Profile, RenderedConfig, ValidationIssue


SUPPORTED_VARIABLE_TYPES = {"string", "ipv4", "bool", "int"}
BUILTIN_VARIABLES = {"profile_id"}
VALID_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigEngine:
    def __init__(self, profiles: dict[str, Profile]):
        self.profiles = profiles
        self.profile_lookup = {
            self._normalize_profile_id(profile.id): profile
            for profile in profiles.values()
        }
        self.environment = Environment(
            autoescape=False,
            finalize=_finalize_value,
            trim_blocks=True,
            lstrip_blocks=False,
            undefined=StrictUndefined,
        )

    def validate_profiles(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for profile in self.profiles.values():
            if not profile.blocks:
                issues.append(
                    ValidationIssue(
                        level="error",
                        scope="profile",
                        message="최소 1개 이상의 blocks가 필요합니다.",
                        source=profile.source,
                        profile_id=profile.id,
                    )
                )

            declared_variables = set(profile.variables) | BUILTIN_VARIABLES
            for variable in profile.variables.values():
                if variable.type not in SUPPORTED_VARIABLE_TYPES:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            scope="profile",
                            message=f"지원하지 않는 변수 타입입니다: {variable.type}",
                            source=profile.source,
                            profile_id=profile.id,
                        )
                    )
                if not VALID_VARIABLE_NAME_PATTERN.fullmatch(variable.name):
                    issues.append(
                        ValidationIssue(
                            level="error",
                            scope="profile",
                            message=(
                                f"변수명은 공백 없이 영문/숫자/언더바(_)만 사용할 수 있습니다: {variable.name}"
                            ),
                            source=profile.source,
                            profile_id=profile.id,
                        )
                    )

            for block in profile.blocks:
                for line in block.lines:
                    issues.extend(
                        self._validate_template_line(
                            line=line,
                            declared_variables=declared_variables,
                            profile=profile,
                            block_name=block.name,
                        )
                    )
        return issues

    def validate_device_records(self, records: Iterable[DeviceRecord]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for record in records:
            if not record.profile_id:
                issues.append(
                    ValidationIssue(
                        level="error",
                        scope="device",
                        message="profile_id 값이 비어 있습니다.",
                        device_id=record.display_name,
                        row_number=record.row_number,
                    )
                )
                continue

            profile = self._get_profile(record.profile_id)
            if not profile:
                issues.append(
                    ValidationIssue(
                        level="error",
                        scope="device",
                        message="profile_id에 해당하는 템플릿을 찾을 수 없습니다.",
                        profile_id=record.profile_id,
                        device_id=record.display_name,
                        row_number=record.row_number,
                    )
                )
                continue

            _, record_issues = self.resolve_values(record, profile)
            issues.extend(record_issues)
        return issues

    def resolve_values(
        self, record: DeviceRecord, profile: Profile
    ) -> tuple[dict[str, Any], list[ValidationIssue]]:
        resolved = dict(record.values)
        issues: list[ValidationIssue] = []

        for variable_name, spec in profile.variables.items():
            raw_value = record.values.get(variable_name, "")
            if raw_value == "":
                if spec.default is not None:
                    resolved_value = spec.default
                elif spec.required:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            scope="device",
                            message=f"필수 변수값이 없습니다: {variable_name}",
                            profile_id=profile.id,
                            device_id=record.display_name,
                            row_number=record.row_number,
                        )
                    )
                    continue
                else:
                    resolved_value = None
            else:
                resolved_value = raw_value

            try:
                resolved[variable_name] = _coerce_value(variable_name, resolved_value, spec.type)
            except ValueError as exc:
                issues.append(
                    ValidationIssue(
                        level="error",
                        scope="device",
                        message=str(exc),
                        profile_id=profile.id,
                        device_id=record.display_name,
                        row_number=record.row_number,
                    )
                )

        resolved["profile_id"] = record.profile_id
        return resolved, issues

    def render_device(
        self,
        record: DeviceRecord,
        *,
        skip_blocks: set[str] | None = None,
    ) -> RenderedConfig:
        profile = self._get_profile(record.profile_id)
        if not profile:
            raise ValueError(f"템플릿을 찾을 수 없습니다: {record.profile_id}")
        context, issues = self.resolve_values(record, profile)
        error_messages = [issue.message for issue in issues if issue.level == "error"]
        if error_messages:
            raise ValueError("; ".join(error_messages))

        rendered_lines: list[str] = []
        for block in profile.blocks:
            if skip_blocks and block.name in skip_blocks:
                continue

            block_lines: list[str] = []
            for line in block.lines:
                template = self.environment.from_string(line)
                rendered = template.render(**context).rstrip()
                if rendered.strip():
                    block_lines.append(rendered)

            if block_lines:
                if rendered_lines:
                    rendered_lines.append("")
                rendered_lines.extend(block_lines)

        return RenderedConfig(
            device_id=record.device_id,
            profile_id=record.profile_id,
            text="\n".join(rendered_lines),
            values=context,
            display_name=record.display_name,
        )

    def render_all(
        self,
        records: Iterable[DeviceRecord],
        *,
        skip_blocks: set[str] | None = None,
    ) -> list[RenderedConfig]:
        return [self.render_device(record, skip_blocks=skip_blocks) for record in records]

    def _validate_template_line(
        self,
        line: str,
        declared_variables: set[str],
        profile: Profile,
        block_name: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        try:
            parsed = self.environment.parse(line)
        except TemplateSyntaxError as exc:
            issues.append(
                ValidationIssue(
                    level="error",
                    scope="profile",
                    message=f"템플릿 문법 오류 ({block_name}): {exc.message}",
                    source=profile.source,
                    profile_id=profile.id,
                )
            )
            return issues

        undeclared = meta.find_undeclared_variables(parsed) - declared_variables
        for variable_name in sorted(undeclared):
            issues.append(
                ValidationIssue(
                    level="error",
                    scope="profile",
                    message=f"정의되지 않은 변수를 사용했습니다 ({block_name}): {variable_name}",
                    source=profile.source,
                    profile_id=profile.id,
                )
            )
        return issues

    def _get_profile(self, profile_id: str) -> Profile | None:
        return self.profiles.get(profile_id) or self.profile_lookup.get(
            self._normalize_profile_id(profile_id)
        )

    @staticmethod
    def _normalize_profile_id(profile_id: str) -> str:
        return str(profile_id).strip().casefold()


def build_bundle_text(rendered_configs: Iterable[RenderedConfig]) -> str:
    chunks: list[str] = []
    for config in rendered_configs:
        device_label = config.display_name or config.device_id or config.profile_id or "-"
        header = f"##### DEVICE: {device_label} | PROFILE: {config.profile_id} #####"
        chunks.append(f"{header}\n{config.text}".strip())
    return "\n\n".join(chunks)


def _coerce_value(variable_name: str, value: Any, value_type: str) -> Any:
    if value is None:
        return None

    if value_type == "string":
        return str(value).strip()

    if value_type == "ipv4":
        try:
            return str(ipaddress.ip_address(str(value).strip()))
        except ValueError as exc:
            raise ValueError(f"IPv4 형식이 잘못되었습니다 ({variable_name}): {value}") from exc

    if value_type == "bool":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
        raise ValueError(f"bool 형식이 잘못되었습니다 ({variable_name}): {value}")

    if value_type == "int":
        try:
            return int(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"정수 형식이 잘못되었습니다 ({variable_name}): {value}") from exc

    raise ValueError(f"지원하지 않는 변수 타입입니다: {value_type}")


def _finalize_value(value: Any) -> Any:
    if value is None:
        return ""
    return value
