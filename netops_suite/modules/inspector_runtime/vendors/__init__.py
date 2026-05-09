# vendors/__init__.py

import os
import sys
import json
import importlib
import importlib.util
import pkgutil
import logging
from collections import defaultdict
from pathlib import Path

import yaml

from core.path_utils import get_app_dir

logger = logging.getLogger(__name__)

# 각 벤더 모듈에서 필요한 딕셔너리들을 임포트
from .axgate import AXGATE_INSPECTION_COMMANDS, AXGATE_BACKUP_COMMANDS, AXGATE_PARSING_RULES
from .cisco import CISCO_INSPECTION_COMMANDS, CISCO_BACKUP_COMMANDS, CISCO_PARSING_RULES
from .aruba import ARUBA_INSPECTION_COMMANDS, ARUBA_BACKUP_COMMANDS, ARUBA_PARSING_RULES
from .alcatel_lucent import ALCATEL_LUCENT_INSPECTION_COMMANDS, ALCATEL_LUCENT_BACKUP_COMMANDS, ALCATEL_LUCENT_PARSING_RULES
from .juniper import JUNIPER_INSPECTION_COMMANDS, JUNIPER_BACKUP_COMMANDS, JUNIPER_PARSING_RULES
from .nexg import NEXG_INSPECTION_COMMANDS, NEXG_BACKUP_COMMANDS, NEXG_PARSING_RULES
from .ubiquoss import UBIQUOSS_INSPECTION_COMMANDS, UBIQUOSS_BACKUP_COMMANDS, UBIQUOSS_PARSING_RULES
from .piolink import PIOLINK_INSPECTION_COMMANDS, PIOLINK_BACKUP_COMMANDS, PIOLINK_PARSING_RULES
from .handreamnet import HANDREAMNET_INSPECTION_COMMANDS, HANDREAMNET_BACKUP_COMMANDS, HANDREAMNET_PARSING_RULES

# 메인 딕셔너리 초기화 (defaultdict 사용으로 키 존재 여부 확인 불필요)
INSPECTION_COMMANDS = defaultdict(dict)
BACKUP_COMMANDS = defaultdict(dict)
PARSING_RULES = defaultdict(dict)

# 커스텀 파서 함수들을 담을 딕셔너리
CUSTOM_PARSERS = {}

# 커스텀 벤더/OS -> Netmiko device_type 매핑
CONNECTION_OVERRIDES = defaultdict(dict)

# 커스텀 벤더/OS -> 핸들러 동작 오버라이드
HANDLER_OVERRIDES = defaultdict(dict)

# custom_rules에서 정의된 벤더/OS 목록
CUSTOM_RULE_PAIRS: set[tuple[str, str]] = set()

_VENDOR_MODULE_NAMES = [
    "alcatel_lucent", "aruba", "axgate", "cisco", "dayou",
    "handreamnet", "juniper", "nexg", "piolink", "ruckus", "ubiquoss",
]


def _discover_vendor_names() -> list[str]:
    """벤더 모듈 이름 목록을 반환합니다. frozen 모드에서도 동작합니다."""
    if getattr(sys, "frozen", False):
        return list(_VENDOR_MODULE_NAMES)

    pkg_path = os.path.dirname(__file__)
    names: list[str] = []
    for _, name, _ in pkgutil.iter_modules([pkg_path]):
        if name not in ("base", "__init__"):
            names.append(name)
    return names or list(_VENDOR_MODULE_NAMES)


def _load_vendor_modules():
    """
    vendors 패키지 내의 모든 모듈을 동적으로 임포트하고,
    각 모듈의 명령어, 파싱 규칙, 커스텀 파서 함수를 자동으로 로드합니다.
    """
    pkg_name = "vendors"

    for name in _discover_vendor_names():
        try:
            module = importlib.import_module(f'.{name}', pkg_name)
            
            # --- 명령어 및 파싱 규칙 로드 ---
            # vendor_name = name.split('_')[0] # ex) 'alcatel_lucent' -> 'alcatel-lucent'
            # if 'alcatel' in vendor_name: vendor_name = 'alcatel-lucent'
            vendor_name = name.replace('_', '-') # ex) 'alcatel_lucent' -> 'alcatel-lucent'

            # 1. 점검 명령어 로드
            cmd_dict_name = f"{name.upper()}_INSPECTION_COMMANDS"
            if hasattr(module, cmd_dict_name):
                vendor_cmds = getattr(module, cmd_dict_name)
                if vendor_name in vendor_cmds:
                    INSPECTION_COMMANDS[vendor_name].update(vendor_cmds[vendor_name])

            # 2. 백업 명령어 로드
            backup_cmd_dict_name = f"{name.upper()}_BACKUP_COMMANDS"
            if hasattr(module, backup_cmd_dict_name):
                vendor_backup_cmds = getattr(module, backup_cmd_dict_name)
                if vendor_name in vendor_backup_cmds:
                    BACKUP_COMMANDS[vendor_name].update(vendor_backup_cmds[vendor_name])

            # 3. 파싱 규칙 로드
            rules_dict_name = f"{name.upper()}_PARSING_RULES"
            if hasattr(module, rules_dict_name):
                vendor_rules = getattr(module, rules_dict_name)
                if vendor_name in vendor_rules:
                    PARSING_RULES[vendor_name].update(vendor_rules[vendor_name])
            
            # --- 커스텀 파서 함수 로드 ---
            for attr_name in dir(module):
                if attr_name.startswith('parsing_'):
                    attr = getattr(module, attr_name)
                    if callable(attr):
                        CUSTOM_PARSERS[attr_name] = attr
                        logger.debug("커스텀 파서 등록: %s", attr_name)

        except Exception as e:
            logger.error("벤더 모듈 '%s' 로드 실패: %s", name, e)

def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()

def _mark_custom_pair(vendor: str, os_name: str) -> None:
    if vendor and os_name:
        CUSTOM_RULE_PAIRS.add((vendor, os_name))

def _merge_inspection_commands(custom_commands: dict) -> None:
    if not isinstance(custom_commands, dict):
        return

    for vendor_key, os_map in custom_commands.items():
        vendor = _normalize_key(vendor_key)
        if not vendor or not isinstance(os_map, dict):
            continue
        for os_key, commands in os_map.items():
            os_name = _normalize_key(os_key)
            if not os_name or not isinstance(commands, list):
                continue
            _mark_custom_pair(vendor, os_name)
            cleaned = [str(cmd).strip() for cmd in commands if isinstance(cmd, str) and cmd.strip()]
            if not cleaned:
                continue
            existing = INSPECTION_COMMANDS.get(vendor, {}).get(os_name, [])
            merged = list(existing)
            for cmd in cleaned:
                if cmd not in merged:
                    merged.append(cmd)
            INSPECTION_COMMANDS[vendor][os_name] = merged

def _merge_backup_commands(custom_commands: dict) -> None:
    if not isinstance(custom_commands, dict):
        return

    for vendor_key, os_map in custom_commands.items():
        vendor = _normalize_key(vendor_key)
        if not vendor or not isinstance(os_map, dict):
            continue
        for os_key, command in os_map.items():
            os_name = _normalize_key(os_key)
            if not os_name or not isinstance(command, str) or not command.strip():
                continue
            _mark_custom_pair(vendor, os_name)
            BACKUP_COMMANDS[vendor][os_name] = command.strip()

def _merge_parsing_rules(custom_rules: dict) -> None:
    if not isinstance(custom_rules, dict):
        return

    for vendor_key, os_map in custom_rules.items():
        vendor = _normalize_key(vendor_key)
        if not vendor or not isinstance(os_map, dict):
            continue
        for os_key, command_map in os_map.items():
            os_name = _normalize_key(os_key)
            if not os_name or not isinstance(command_map, dict):
                continue
            _mark_custom_pair(vendor, os_name)
            for command, rules in command_map.items():
                if not isinstance(command, str) or not command.strip():
                    continue
                if not isinstance(rules, dict):
                    continue
                PARSING_RULES[vendor].setdefault(os_name, {})
                PARSING_RULES[vendor][os_name][command.strip()] = rules

def _normalize_device_type(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()

# handler_overrides에서 허용하는 키 목록
_HANDLER_OVERRIDE_KEYS = {
    "handler_type",           # "paramiko" | "netmiko"  (기본: paramiko)
    "enable_command",         # enable 명령어 (기본: "enable")
    "disable_paging_command", # 페이지네이션 비활성화 명령어 (기본: "terminal length 0", 빈 문자열이면 비활성)
    "prompt_pattern",         # 프롬프트 감지 정규식 (기본: "[>#]\\s*$")
    "initial_delay",          # 접속 후 대기 시간 초 (기본: 1.0)
    "command_delay",          # 명령어 전송 후 대기 시간 초 (기본: 2.0)
    "read_delay",             # 채널 읽기 간격 초 (기본: 0.2)
    "more_pattern",           # 페이지네이션 패턴 (기본: "--More--")
    "more_response",          # 페이지네이션 응답 문자 (기본: " ")
    "shell_width",            # invoke_shell width (기본: 200)
    "shell_height",           # invoke_shell height (기본: 1000)
    "skip_enable",            # enable 건너뛰기 여부 (기본: false)
}


def _merge_handler_overrides(custom_rules: dict) -> None:
    if not isinstance(custom_rules, dict):
        return

    for vendor_key, os_map in custom_rules.items():
        vendor = _normalize_key(vendor_key)
        if not vendor or not isinstance(os_map, dict):
            continue
        for os_key, overrides in os_map.items():
            os_name = _normalize_key(os_key)
            if not os_name or not isinstance(overrides, dict):
                continue
            _mark_custom_pair(vendor, os_name)

            cleaned: dict = {}
            for key, value in overrides.items():
                if key not in _HANDLER_OVERRIDE_KEYS:
                    logger.warning(
                        f"handler_overrides: 알 수 없는 키 '{key}' 무시 "
                        f"(vendor={vendor}, os={os_name})"
                    )
                    continue
                cleaned[key] = value

            if cleaned:
                HANDLER_OVERRIDES[vendor][os_name] = cleaned


def _merge_connection_overrides(custom_rules: dict) -> None:
    if not isinstance(custom_rules, dict):
        return

    for vendor_key, os_map in custom_rules.items():
        vendor = _normalize_key(vendor_key)
        if not vendor or not isinstance(os_map, dict):
            continue
        for os_key, override in os_map.items():
            os_name = _normalize_key(os_key)
            if not os_name:
                continue
            _mark_custom_pair(vendor, os_name)

            if isinstance(override, str):
                device_type = _normalize_device_type(override)
                if not device_type:
                    continue
                CONNECTION_OVERRIDES[vendor][os_name] = {"default": device_type}
                continue

            if not isinstance(override, dict):
                continue

            mapped: dict[str, str] = {}
            for conn_key, device_type in override.items():
                conn = _normalize_key(conn_key)
                if conn not in {"ssh", "telnet", "default", "any"}:
                    continue
                normalized_device_type = _normalize_device_type(device_type)
                if normalized_device_type:
                    mapped[conn] = normalized_device_type

            if mapped:
                CONNECTION_OVERRIDES[vendor][os_name] = mapped

def _load_custom_rules() -> None:
    app_dir = get_app_dir()
    yaml_path = app_dir / "custom_rules.yaml"
    json_path = app_dir / "custom_rules.json"

    data: dict | None = None

    if yaml_path.exists():
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("custom_rules.yaml 로드 실패: %s", e)
            return
    elif json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("custom_rules.json 로드 실패: %s", e)
            return
    else:
        return

    if not isinstance(data, dict):
        logger.error("custom_rules 형식 오류: 최상위가 dict가 아닙니다.")
        return

    _merge_inspection_commands(data.get("inspection_commands", {}))
    _merge_backup_commands(data.get("backup_commands", {}))
    _merge_parsing_rules(data.get("parsing_rules", {}))
    _merge_connection_overrides(data.get("connection_overrides", {}))
    _merge_handler_overrides(data.get("handler_overrides", {}))


def _load_user_custom_parsers() -> None:
    parser_dir = get_app_dir() / "custom_parsers"
    if not parser_dir.exists():
        return
    for path in sorted(parser_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"netops_user_parser_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for attr_name in dir(module):
                if attr_name.startswith("parsing_"):
                    attr = getattr(module, attr_name)
                    if callable(attr):
                        CUSTOM_PARSERS[attr_name] = attr
                        logger.debug("사용자 파서 등록: %s (%s)", attr_name, path)
        except Exception as exc:
            logger.error("사용자 파서 로드 실패: %s - %s", path, exc)

def is_custom_rule_pair(vendor: str, os_name: str) -> bool:
    vendor_key = _normalize_key(vendor)
    os_key = _normalize_key(os_name)
    if not vendor_key or not os_key:
        return False
    return (vendor_key, os_key) in CUSTOM_RULE_PAIRS

_load_vendor_modules()
_load_user_custom_parsers()
_load_custom_rules()

# get_custom_handler 함수는 base에서 직접 임포트하여 사용하도록 변경
from .base import get_custom_handler

__all__ = [
    'INSPECTION_COMMANDS',
    'BACKUP_COMMANDS',
    'PARSING_RULES',
    'CUSTOM_PARSERS',
    'CONNECTION_OVERRIDES',
    'HANDLER_OVERRIDES',
    'CUSTOM_RULE_PAIRS',
    'is_custom_rule_pair',
    'get_custom_handler',
] 
