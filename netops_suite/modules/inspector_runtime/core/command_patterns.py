from __future__ import annotations

import math
import numbers
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from .custom_exceptions import ValidationError


VARIABLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
FORBIDDEN_VARIABLES = {"password", "enable_password"}
FORBIDDEN_SUBSTITUTION_CHARACTERS = frozenset(";&|<>")
MAX_REPORTED_ERRORS = 20


@dataclass(frozen=True, slots=True)
class ParsedCommandPattern:
    text: str
    variable_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedCustomCommands:
    command_count: int
    variable_names: tuple[str, ...]
    commands_by_device: dict[str, list[str]]


def prepare_custom_commands(
    command_patterns: Iterable[str],
    devices: list[dict[str, Any]],
) -> PreparedCustomCommands:
    parsed_patterns: list[ParsedCommandPattern] = []
    syntax_errors: list[str] = []
    ordered_variables: list[str] = []
    seen_variables: set[str] = set()

    for command_index, raw_pattern in enumerate(command_patterns, start=1):
        if not isinstance(raw_pattern, str):
            syntax_errors.append(
                f"명령 {command_index}: 명령은 문자열이어야 합니다."
            )
            continue
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        try:
            parsed = parse_command_pattern(pattern)
        except ValueError as exc:
            syntax_errors.append(f"명령 {command_index}: {exc}")
            continue
        parsed_patterns.append(parsed)
        for variable_name in parsed.variable_names:
            if variable_name not in seen_variables:
                seen_variables.add(variable_name)
                ordered_variables.append(variable_name)

    if not parsed_patterns and not syntax_errors:
        syntax_errors.append("실행할 사용자 명령이 없습니다.")
    if syntax_errors:
        raise ValidationError(_format_errors(syntax_errors))

    value_errors: list[str] = []
    commands_by_device: dict[str, list[str]] = {}
    for row_number, device in enumerate(devices, start=2):
        device_ip = str(device.get("ip", "N/A")).strip() or "N/A"
        rendered_commands: list[str] = []
        for command_index, parsed in enumerate(parsed_patterns, start=1):
            rendered_values: dict[str, str] = {}
            command_has_error = False
            for variable_name in dict.fromkeys(parsed.variable_names):
                if variable_name not in device:
                    value_errors.append(
                        _device_error(
                            row_number,
                            device_ip,
                            command_index,
                            variable_name,
                            "장비 목록에 해당 열이 없습니다.",
                        )
                    )
                    command_has_error = True
                    continue
                value = device[variable_name]
                if _is_missing(value):
                    value_errors.append(
                        _device_error(
                            row_number,
                            device_ip,
                            command_index,
                            variable_name,
                            "값이 비어 있습니다.",
                        )
                    )
                    command_has_error = True
                    continue
                raw_value = str(value)
                if any(
                    unicodedata.category(character) == "Cc"
                    for character in raw_value
                ):
                    value_errors.append(
                        _device_error(
                            row_number,
                            device_ip,
                            command_index,
                            variable_name,
                            "값에 제어 문자를 사용할 수 없습니다.",
                        )
                    )
                    command_has_error = True
                    continue
                rendered_value = _format_value(value)
                if not rendered_value:
                    value_errors.append(
                        _device_error(
                            row_number,
                            device_ip,
                            command_index,
                            variable_name,
                            "값이 비어 있습니다.",
                        )
                    )
                    command_has_error = True
                    continue
                if any(
                    character in FORBIDDEN_SUBSTITUTION_CHARACTERS
                    for character in rendered_value
                ):
                    value_errors.append(
                        _device_error(
                            row_number,
                            device_ip,
                            command_index,
                            variable_name,
                            "값에 CLI 구분자 또는 리디렉션 문자(; & | < >)를 사용할 수 없습니다.",
                        )
                    )
                    command_has_error = True
                    continue
                rendered_values[variable_name] = rendered_value

            if command_has_error:
                continue
            rendered_commands.append(render_command_pattern(parsed, rendered_values))

        commands_by_device[device_ip] = rendered_commands

    if value_errors:
        raise ValidationError(_format_errors(value_errors))

    return PreparedCustomCommands(
        command_count=len(parsed_patterns),
        variable_names=tuple(ordered_variables),
        commands_by_device=commands_by_device,
    )


def parse_command_pattern(pattern: str) -> ParsedCommandPattern:
    if any(marker in pattern for marker in ("{%", "%}", "{#", "#}")):
        raise ValueError("조건문, 주석 또는 Jinja 제어 문법은 사용할 수 없습니다.")

    variable_names: list[str] = []
    position = 0
    while position < len(pattern):
        opening = pattern.find("{{", position)
        closing = pattern.find("}}", position)
        if closing != -1 and (opening == -1 or closing < opening):
            raise ValueError("여는 '{{' 없이 닫는 '}}'가 있습니다.")
        if opening == -1:
            break

        end = pattern.find("}}", opening + 2)
        if end == -1:
            raise ValueError("변수 표현식을 닫는 '}}'가 없습니다.")
        if pattern.find("{{", opening + 2, end) != -1:
            raise ValueError("중첩된 변수 표현식은 사용할 수 없습니다.")

        expression = pattern[opening + 2 : end].strip()
        if not VARIABLE_NAME_PATTERN.fullmatch(expression):
            raise ValueError(
                "변수는 영문 소문자, 숫자, 언더바만 사용하고 숫자로 시작할 수 없습니다."
            )
        if expression in FORBIDDEN_VARIABLES:
            raise ValueError(f"인증정보 변수 '{{{{ {expression} }}}}'는 사용할 수 없습니다.")
        variable_names.append(expression)
        position = end + 2

    if pattern.find("}}", position) != -1:
        raise ValueError("여는 '{{' 없이 닫는 '}}'가 있습니다.")

    return ParsedCommandPattern(pattern, tuple(variable_names))


def render_command_pattern(
    parsed: ParsedCommandPattern,
    values: dict[str, str],
) -> str:
    rendered: list[str] = []
    position = 0
    variable_index = 0
    while variable_index < len(parsed.variable_names):
        opening = parsed.text.find("{{", position)
        end = parsed.text.find("}}", opening + 2)
        rendered.append(parsed.text[position:opening])
        variable_name = parsed.variable_names[variable_index]
        rendered.append(values[variable_name])
        position = end + 2
        variable_index += 1
    rendered.append(parsed.text[position:])
    return "".join(rendered).strip()


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(missing)
    except (TypeError, ValueError):
        return False


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).strip()
    if isinstance(value, numbers.Real):
        numeric_value = float(value)
        if math.isfinite(numeric_value) and numeric_value.is_integer():
            return str(int(numeric_value))
    return str(value).strip()


def _device_error(
    row_number: int,
    device_ip: str,
    command_index: int,
    variable_name: str,
    message: str,
) -> str:
    return (
        f"Excel {row_number}행 · 장비 {device_ip} · 명령 {command_index} · "
        f"변수 {variable_name}: {message}"
    )


def _format_errors(errors: list[str]) -> str:
    visible_errors = errors[:MAX_REPORTED_ERRORS]
    lines = ["사용자 명령 변수 검증에 실패했습니다."]
    lines.extend(f"- {error}" for error in visible_errors)
    if len(errors) > MAX_REPORTED_ERRORS:
        lines.append(
            f"- 나머지 {len(errors) - MAX_REPORTED_ERRORS}건은 생략했습니다. "
            f"(전체 {len(errors)}건)"
        )
    return "\n".join(lines)
