from __future__ import annotations

import json
import locale
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.ai_models import AiProviderConfig, CliInvocation


MAX_ARGUMENT_PROMPT_CHARS = 28000
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
RESERVED_EXTRA_ARG_FLAGS = {
    "--help",
    "--version",
    "--model",
    "--image",
    "--output-format",
    "--json",
    "--verbose",
    "--dangerously-bypass-approvals-and-sandbox",
}


def diagnose_cli_error(provider_key: str, detail: str) -> str:
    text = detail.strip()
    if not text:
        return ""
    if provider_key == "codex" and _is_codex_service_tier_config_error(text):
        config_path = _extract_codex_config_path(text)
        location = f"\n설정 파일: {config_path}" if config_path else ""
        return (
            "Codex CLI 설정 파일을 읽지 못했습니다."
            f"{location}\n\n"
            '현재 설치된 Codex CLI는 service_tier 값으로 "fast" 또는 "flex"만 지원합니다. '
            'config.toml에서 service_tier = "priority"를 service_tier = "fast" 또는 '
            'service_tier = "flex"로 바꾼 뒤 다시 로그인하세요.\n\n'
            "이 값은 NetOps Suite의 추가 인자가 아니라 Codex 전역 설정입니다.\n\n"
            f"원본 오류:\n{text}"
        )
    return text


def is_blocking_cli_configuration_error(provider_key: str, detail: str) -> bool:
    text = detail.strip()
    return provider_key == "codex" and _is_codex_service_tier_config_error(text)


@dataclass(frozen=True, slots=True)
class CliConfigurationRepairResult:
    attempted: bool
    repaired: bool
    message: str
    config_path: str = ""
    backup_path: str = ""


def repair_cli_configuration_error(
    provider_key: str,
    detail: str,
    *,
    replacement_service_tier: str = "fast",
) -> CliConfigurationRepairResult:
    if not is_blocking_cli_configuration_error(provider_key, detail):
        return CliConfigurationRepairResult(attempted=False, repaired=False, message="")

    config_path_text = _extract_codex_config_path(detail)
    if not config_path_text:
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            message="Codex 설정 파일 경로를 찾지 못해 자동 복구하지 못했습니다.",
        )

    config_path = Path(config_path_text)
    if not config_path.exists():
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            config_path=str(config_path),
            message=f"Codex 설정 파일이 없어 자동 복구하지 못했습니다.\n설정 파일: {config_path}",
        )

    try:
        original = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            config_path=str(config_path),
            message=f"Codex 설정 파일을 읽지 못해 자동 복구하지 못했습니다.\n설정 파일: {config_path}\n{exc}",
        )

    pattern = re.compile(r"(?m)^(\s*service_tier\s*=\s*)([\"'])([^\"']+)([\"'])(\s*(?:#.*)?)$")
    match = pattern.search(original)
    if not match:
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            config_path=str(config_path),
            message=(
                "Codex 설정 파일에서 service_tier 항목을 찾지 못해 자동 복구하지 못했습니다.\n"
                f"설정 파일: {config_path}"
            ),
        )

    previous_value = match.group(3)
    if previous_value in {"fast", "flex"}:
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            config_path=str(config_path),
            message=f"Codex service_tier는 이미 호환되는 값입니다: {previous_value}",
        )

    backup_path = config_path.with_name(
        f"{config_path.name}.bak-netops-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    repaired = pattern.sub(
        lambda item: f'{item.group(1)}"{replacement_service_tier}"{item.group(5)}',
        original,
        count=1,
    )

    try:
        backup_path.write_text(original, encoding="utf-8")
        config_path.write_text(repaired, encoding="utf-8")
    except OSError as exc:
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            config_path=str(config_path),
            backup_path=str(backup_path),
            message=f"Codex 설정 파일을 쓰지 못해 자동 복구하지 못했습니다.\n설정 파일: {config_path}\n{exc}",
        )

    return CliConfigurationRepairResult(
        attempted=True,
        repaired=True,
        config_path=str(config_path),
        backup_path=str(backup_path),
        message=(
            "Codex 설정을 자동 복구했습니다.\n"
            f"설정 파일: {config_path}\n"
            f"백업 파일: {backup_path}\n"
            f'service_tier = "{previous_value}" -> service_tier = "{replacement_service_tier}"'
        ),
    )


def _is_codex_service_tier_config_error(detail: str) -> bool:
    lowered = detail.casefold()
    return (
        "error loading configuration" in lowered
        and "unknown variant" in lowered
        and ("service_tier" in lowered or ("expected" in lowered and "fast" in lowered and "flex" in lowered))
    )


def _extract_codex_config_path(detail: str) -> str:
    match = re.search(r"Error loading configuration:\s*(.+?config\.toml)(?::\d+:\d+)?", detail, re.IGNORECASE)
    return match.group(1).strip() if match else ""


@dataclass(frozen=True, slots=True)
class CliProviderSpec:
    key: str
    display_name: str
    executable: str
    login_args: tuple[str, ...]
    status_args: tuple[str, ...]
    help_args: tuple[str, ...]
    output_format: str
    prompt_mode: str
    global_args: tuple[str, ...] = field(default_factory=tuple)
    prompt_flag: str = ""
    model_flag: str = "--model"
    docs_url: str = ""
    install_hint: str = ""
    chat_args_before_prompt: tuple[str, ...] = field(default_factory=tuple)
    chat_args_after_prompt: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    key: str
    display_name: str
    executable: str
    resolved_path: str
    installed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class CliHelpOption:
    flag: str
    description: str = ""
    value_hint: str = ""
    takes_value: bool = False
    short_flag: str = ""


PROVIDER_SPECS: dict[str, CliProviderSpec] = {
    "codex": CliProviderSpec(
        key="codex",
        display_name="ChatGPT Codex",
        executable="codex",
        login_args=("login",),
        status_args=("login", "status"),
        help_args=("exec", "--help"),
        output_format="jsonl",
        prompt_mode="stdin",
        chat_args_before_prompt=("exec", "--json"),
        chat_args_after_prompt=("-",),
        docs_url="https://developers.openai.com/codex",
        install_hint="Codex CLI를 설치한 뒤 codex login을 실행하세요.",
    ),
    "claude": CliProviderSpec(
        key="claude",
        display_name="Claude Code",
        executable="claude",
        login_args=("auth", "login"),
        status_args=("auth", "status", "--text"),
        help_args=("--help",),
        output_format="stream-json",
        prompt_mode="argument",
        prompt_flag="-p",
        chat_args_after_prompt=("--output-format", "stream-json", "--verbose"),
        docs_url="https://docs.anthropic.com/en/docs/claude-code",
        install_hint="Claude Code를 설치한 뒤 claude auth login을 실행하세요.",
    ),
    "gemini": CliProviderSpec(
        key="gemini",
        display_name="Gemini CLI",
        executable="gemini",
        login_args=(),
        status_args=("--version",),
        help_args=("--help",),
        output_format="json",
        prompt_mode="argument",
        prompt_flag="-p",
        chat_args_after_prompt=("--output-format", "json"),
        docs_url="https://google-gemini.github.io/gemini-cli/",
        install_hint="Gemini CLI를 설치한 뒤 gemini를 실행해 Google 로그인을 완료하세요.",
    ),
}


MODEL_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "codex": (
        ("CLI 기본값", ""),
        ("GPT-5.5", "gpt-5.5"),
        ("GPT-5.4", "gpt-5.4"),
        ("GPT-5.4 Mini", "gpt-5.4-mini"),
        ("GPT-5.3 Codex Spark", "gpt-5.3-codex-spark"),
    ),
    "claude": (
        ("CLI 기본값", ""),
        ("Fable alias", "fable"),
        ("Opus alias", "opus"),
        ("Sonnet alias", "sonnet"),
        ("Claude Opus 4.8", "claude-opus-4-8"),
        ("Claude Sonnet 5", "claude-sonnet-5"),
        ("Claude Haiku 4.5", "claude-haiku-4-5"),
    ),
    "gemini": (
        ("CLI 기본값", ""),
        ("Gemini 3 Pro Preview", "gemini-3-pro-preview"),
        ("Gemini 3 Flash Preview", "gemini-3-flash-preview"),
        ("Gemini Flash Latest", "gemini-flash-latest"),
        ("Gemini 3.5 Flash", "gemini-3.5-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
    ),
}


def provider_spec(key: str) -> CliProviderSpec:
    try:
        return PROVIDER_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown AI provider: {key}") from exc


def model_options_for_provider(key: str, current_model: str = "") -> list[tuple[str, str]]:
    options = list(MODEL_OPTIONS.get(key, (("CLI 기본값", ""),)))
    selected = current_model.strip()
    if selected and selected not in {value for _label, value in options}:
        options.append((f"사용자 설정: {selected}", selected))
    return options


def provider_configs_from_app_config(ai_config: dict[str, Any]) -> dict[str, AiProviderConfig]:
    providers = ai_config.get("providers", {}) if isinstance(ai_config, dict) else {}
    return {key: AiProviderConfig.from_dict(key, providers.get(key, {})) for key in PROVIDER_SPECS}


def resolve_provider_program(config: AiProviderConfig) -> str:
    spec = provider_spec(config.key)
    configured = config.command_path.strip()
    if configured:
        expanded = str(Path(configured).expanduser())
        if Path(expanded).exists():
            return expanded
        found = shutil.which(configured)
        if found and not _is_windowsapps_alias(found):
            return found
        if found and _is_windowsapps_alias(found):
            for candidate in _provider_program_candidates(spec):
                if candidate:
                    return candidate
        return expanded
    for candidate in _provider_program_candidates(spec):
        if candidate:
            return candidate
    return spec.executable


def inspect_provider(config: AiProviderConfig) -> ProviderHealth:
    spec = provider_spec(config.key)
    program = resolve_provider_program(config)
    resolved = shutil.which(program) if not Path(program).exists() else program
    installed = bool(resolved)
    if installed and _is_windowsapps_alias(str(resolved)):
        installed = False
        detail = (
            "WindowsApps 실행 별칭은 직접 실행이 거부될 수 있습니다. "
            f"Command에 실제 CLI 실행 파일 경로를 지정하세요. {spec.install_hint}"
        )
    else:
        detail = str(resolved or spec.install_hint)
    return ProviderHealth(
        key=config.key,
        display_name=spec.display_name,
        executable=spec.executable,
        resolved_path=str(resolved or ""),
        installed=installed,
        detail=detail,
    )


def _provider_program_candidates(spec: CliProviderSpec) -> list[str]:
    candidates: list[str] = []
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        if spec.key == "codex" and local_appdata:
            candidates.extend(
                [
                    str(Path(local_appdata) / "OpenAI" / "Codex" / "bin" / "codex.exe"),
                    str(
                        Path(local_appdata)
                        / "Packages"
                        / "OpenAI.Codex_2p2nqsd0c76g0"
                        / "LocalCache"
                        / "Local"
                        / "OpenAI"
                        / "Codex"
                        / "bin"
                        / "codex.exe"
                    ),
                ]
            )
        if spec.key in {"claude", "gemini"}:
            command_name = f"{spec.executable}.cmd"
            candidates.extend(
                [
                    shutil.which(command_name) or "",
                    str(Path(appdata) / "npm" / command_name) if appdata else "",
                    shutil.which(spec.executable) or "",
                ]
            )
    candidates.append(shutil.which(spec.executable) or "")
    candidates.append(spec.executable)

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        if _is_windowsapps_alias(text):
            continue
        if Path(text).exists() or shutil.which(text):
            unique.append(text)
    return unique


def _is_windowsapps_alias(path: str) -> bool:
    return "windowsapps" in path.casefold()


def build_login_invocation(config: AiProviderConfig, working_dir: str = "") -> CliInvocation:
    spec = provider_spec(config.key)
    return CliInvocation(
        provider_key=config.key,
        program=resolve_provider_program(config),
        args=[*spec.global_args, *spec.login_args],
        working_dir=working_dir,
        timeout_seconds=config.timeout_seconds,
    )


def build_status_invocation(config: AiProviderConfig, working_dir: str = "") -> CliInvocation:
    spec = provider_spec(config.key)
    return CliInvocation(
        provider_key=config.key,
        program=resolve_provider_program(config),
        args=[*spec.global_args, *spec.status_args],
        working_dir=working_dir,
        timeout_seconds=min(config.timeout_seconds, 60),
    )


def build_help_invocation(config: AiProviderConfig, working_dir: str = "") -> CliInvocation:
    spec = provider_spec(config.key)
    return CliInvocation(
        provider_key=config.key,
        program=resolve_provider_program(config),
        args=[*spec.global_args, *spec.help_args],
        working_dir=working_dir,
        timeout_seconds=min(config.timeout_seconds, 30),
    )


def build_chat_invocation(
    config: AiProviderConfig,
    prompt: str,
    *,
    role_prompt: str = "",
    context: str = "",
    working_dir: str = "",
) -> CliInvocation:
    spec = provider_spec(config.key)
    composed_prompt = compose_agent_prompt(prompt, role_prompt=role_prompt or config.role_prompt, context=context)
    args: list[str] = [*spec.global_args, *spec.chat_args_before_prompt]
    if config.model.strip():
        args.extend([spec.model_flag, config.model.strip()])
    args.extend(config.extra_args)
    stdin_text = ""

    if spec.prompt_mode == "stdin":
        args.extend(spec.chat_args_after_prompt)
        stdin_text = composed_prompt
    elif spec.prompt_mode == "argument":
        if len(composed_prompt) > MAX_ARGUMENT_PROMPT_CHARS:
            raise ValueError(
                f"{spec.display_name} prompt is too long for argument-based CLI mode. "
                "Use a shorter prompt or configure a stdin-capable command override."
            )
        if spec.prompt_flag:
            args.append(spec.prompt_flag)
        args.append(composed_prompt)
        args.extend(spec.chat_args_after_prompt)
    else:
        raise ValueError(f"Unsupported prompt mode for {spec.display_name}: {spec.prompt_mode}")

    return CliInvocation(
        provider_key=config.key,
        program=resolve_provider_program(config),
        args=args,
        stdin_text=stdin_text,
        working_dir=working_dir,
        timeout_seconds=config.timeout_seconds,
    )


def parse_cli_help_options(help_text: str) -> list[CliHelpOption]:
    parsed: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] | None = None
    for raw_line in help_text.splitlines():
        line = sanitize_cli_text(raw_line).rstrip()
        stripped = line.strip()
        if not stripped:
            current = None
            continue

        option = _parse_help_option_line(stripped)
        if option is not None:
            parsed.append(option)
            current = option
            continue

        if current is not None and raw_line[:1].isspace():
            description = str(current.get("description", "") or "")
            if stripped and not stripped.startswith("[possible values:"):
                current["description"] = f"{description} {stripped}".strip()

    options = [
        CliHelpOption(
            flag=str(item["flag"]),
            short_flag=str(item.get("short_flag", "") or ""),
            value_hint=str(item.get("value_hint", "") or ""),
            takes_value=bool(item.get("takes_value", False)),
            description=str(item.get("description", "") or ""),
        )
        for item in parsed
        if item.get("flag")
    ]

    unique: list[CliHelpOption] = []
    seen: set[str] = set()
    for option in options:
        if option.flag in seen:
            continue
        seen.add(option.flag)
        unique.append(option)
    return unique


def extra_arg_options_from_help(help_text: str) -> list[CliHelpOption]:
    return [option for option in parse_cli_help_options(help_text) if option.flag not in RESERVED_EXTRA_ARG_FLAGS]


def _parse_help_option_line(stripped: str) -> dict[str, str | bool] | None:
    if not stripped.startswith("-"):
        return None
    declaration, description = _split_option_declaration(stripped)
    flags = re.findall(r"(?<![\w-])(-[A-Za-z0-9]|--[A-Za-z0-9][A-Za-z0-9-]*)", declaration)
    if not flags:
        return None

    long_flags = [flag for flag in flags if flag.startswith("--")]
    flag = long_flags[0] if long_flags else flags[0]
    short_flag = next((item for item in flags if item.startswith("-") and not item.startswith("--")), "")
    value_hint = _extract_option_value_hint(declaration, flag)
    return {
        "flag": flag,
        "short_flag": short_flag,
        "value_hint": value_hint,
        "takes_value": bool(value_hint),
        "description": description,
    }


def _split_option_declaration(stripped: str) -> tuple[str, str]:
    pieces = re.split(r"\s{2,}", stripped, maxsplit=1)
    if len(pieces) == 2:
        return pieces[0].strip(), pieces[1].strip()
    return stripped, ""


def _extract_option_value_hint(declaration: str, flag: str) -> str:
    match = re.search(rf"{re.escape(flag)}(?:[=\s]+)(<[^>]+>|\[[^\]]+\]|[A-Z][A-Z0-9_-]*(?:\.\.\.)?)", declaration)
    if not match:
        return ""
    return match.group(1).strip()


def compose_agent_prompt(prompt: str, *, role_prompt: str = "", context: str = "") -> str:
    sections = []
    if role_prompt.strip():
        sections.append(f"Agent role:\n{role_prompt.strip()}")
    if context.strip():
        sections.append(f"Shared context:\n{context.strip()}")
    sections.append(f"User request:\n{prompt.strip()}")
    return "\n\n".join(sections).strip()


def extract_text_from_cli_line(line: str) -> str:
    stripped = sanitize_cli_text(line).strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    found = _collect_text_fragments(payload)
    return sanitize_cli_text("".join(found)).strip()


def decode_cli_output(data: bytes) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    candidates = [locale.getpreferredencoding(False), "cp949", "mbcs", "utf-8"]
    best_text = ""
    best_score: tuple[int, int] | None = None
    for encoding in candidates:
        if not encoding:
            continue
        try:
            decoded = data.decode(encoding, errors="replace")
        except LookupError:
            continue
        score = (decoded.count("\ufffd"), -sum("\uac00" <= char <= "\ud7a3" for char in decoded))
        if best_score is None or score < best_score:
            best_score = score
            best_text = decoded
    return best_text or data.decode("utf-8", errors="replace")


def sanitize_cli_text(text: str) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    return cleaned.replace("\x00", "").strip("\r")


def should_ignore_cli_output_text(text: str) -> bool:
    stripped = sanitize_cli_text(text).strip()
    if not stripped:
        return True
    lowered = stripped.casefold()
    replacement_count = stripped.count("\ufffd")

    if replacement_count >= 3 and "pid" in lowered:
        return True
    if "pid" in lowered and "process" in lowered and ("terminated" in lowered or "success" in lowered):
        return True
    if "pid" in lowered and "프로세스" in stripped and ("종료" in stripped or stripped.startswith("성공")):
        return True
    return False


def _collect_text_fragments(value: Any) -> list[str]:
    fragments: list[str] = []
    if isinstance(value, str):
        return []
    if isinstance(value, list):
        for item in value:
            fragments.extend(_collect_text_fragments(item))
        return fragments
    if not isinstance(value, dict):
        return fragments

    for key in ("text", "delta", "message", "output_text", "response"):
        item = value.get(key)
        if isinstance(item, str) and item:
            fragments.append(item)

    content = value.get("content")
    if isinstance(content, str) and content:
        fragments.append(content)
    elif isinstance(content, list):
        for item in content:
            fragments.extend(_collect_text_fragments(item))
    elif isinstance(content, dict):
        fragments.extend(_collect_text_fragments(content))

    for key in ("item", "result", "response", "data"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            fragments.extend(_collect_text_fragments(nested))

    return fragments


def safe_env_for_cli() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")
    env.setdefault("CLICOLOR", "0")
    return env
