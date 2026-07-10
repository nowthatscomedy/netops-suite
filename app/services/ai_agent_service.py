from __future__ import annotations

import json
import locale
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.ai_models import AiProviderConfig, CliInvocation


MAX_ARGUMENT_PROMPT_CHARS = 28000
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
CIDR_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}/(?:[0-9]|[12][0-9]|3[0-2])(?![\d.])")
DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![A-Za-z0-9_-])"
)
MAC_RE = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}(?:[:\-\.\s][0-9a-f]{2}){2,7}|[0-9a-f]{6,12})(?![0-9a-f])"
)
RESERVED_EXTRA_ARG_FLAGS = {
    "-a",
    "--help",
    "--version",
    "--ask-for-approval",
    "--model",
    "--image",
    "--output-format",
    "--json",
    "--verbose",
    "--dangerously-bypass-approvals-and-sandbox",
}
CODEX_NONINTERACTIVE_GLOBAL_ARGS = ("-a", "never", "-s", "read-only")
DNS_RECORD_TYPES = ("AAAA", "CNAME", "PTR", "TXT", "MX", "NS", "A")
DEFAULT_EXTERNAL_PING_TARGETS = ("8.8.8.8", "1.1.1.1", "google.com")


@dataclass(frozen=True, slots=True)
class NetOpsChatAction:
    kind: str
    title: str
    target: str = ""
    port: int = 0
    record_type: str = "A"
    server: str = ""
    resolve_names: bool = True
    interface_name: str = ""
    ip_address: str = ""
    prefix: int = 0
    gateway: str = ""
    dns_servers: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    duration_seconds: int = 0
    interval_seconds: int = 0
    requires_approval: bool = False
    risk_level: str = "low"
    impact: str = ""
    admin_required: bool = False


def plan_netops_chat_action(prompt: str) -> NetOpsChatAction | None:
    text = prompt.strip()
    if not text:
        return None

    lowered = text.casefold()
    target = _extract_netops_target(text)
    port = _extract_netops_port(text)
    mac_address = _extract_netops_mac(text)

    if _is_oui_cache_refresh_request(lowered) and not mac_address:
        return NetOpsChatAction(
            "oui_cache_refresh",
            "OUI cache refresh",
            requires_approval=True,
            risk_level="medium",
            impact="Downloads IEEE OUI registries and rewrites the local vendor cache.",
        )

    if _is_dns_flush_request(lowered):
        return NetOpsChatAction(
            "dns_flush_cache",
            "DNS cache flush",
            requires_approval=True,
            risk_level="medium",
            impact="Clears the local Windows DNS client cache.",
            admin_required=True,
        )

    cidr = _extract_cidr(text)
    if cidr and _contains_any(lowered, ("subnet", "cidr", "서브넷", "대역", "network calculate", "계산")):
        return NetOpsChatAction("subnet_calculate", f"Subnet calculate: {cidr}", target=cidr)

    if _contains_any(lowered, ("oui", "vendor", "벤더", "제조사")) and mac_address:
        return NetOpsChatAction("oui_lookup", f"OUI 제조사 조회: {mac_address}", target=mac_address)

    if _contains_any(lowered, ("공인 ip", "공인아이피", "외부 ip", "public ip", "external ip", "what is my ip", "내 공인")):
        return NetOpsChatAction("public_ip", "공인 IP 확인")

    change_action = _plan_network_change_action(text, lowered)
    if change_action is not None:
        return change_action

    ping_alias_target = _ping_alias_target(lowered)
    if ping_alias_target:
        return NetOpsChatAction("ping", f"Ping: {ping_alias_target}", target=ping_alias_target)

    if _is_external_ping_request(lowered):
        return NetOpsChatAction(
            "external_ping",
            "외부 Ping 테스트",
            targets=DEFAULT_EXTERNAL_PING_TARGETS,
        )

    if _contains_any(lowered, ("tcping", "tcp", "포트", "port", "열려", "열렸", "접속", "연결 확인")) and target and port:
        return NetOpsChatAction("tcp_check", f"TCP 포트 확인: {target}:{port}", target=target, port=port)

    if _contains_any(lowered, ("dns", "nslookup", "도메인 조회", "레코드 조회")) and target:
        record_type = _extract_dns_record_type(text)
        if record_type == "A" and _is_ipv4_address(target) and _contains_any(lowered, ("ptr", "reverse", "역방향")):
            record_type = "PTR"
        return NetOpsChatAction("dns_lookup", f"DNS 조회: {target} {record_type}", target=target, record_type=record_type)

    if _contains_any(lowered, ("pathping", "패스핑")) and target:
        return NetOpsChatAction("pathping", f"pathping: {target}", target=target, resolve_names=not _no_resolve_requested(lowered))

    if _contains_any(lowered, ("tracert", "traceroute", "trace route", "경로 추적", "홉 추적")) and target:
        return NetOpsChatAction("tracert", f"tracert: {target}", target=target, resolve_names=not _no_resolve_requested(lowered))

    if _contains_any(lowered, ("ping", "핑")) and target:
        return NetOpsChatAction("ping", f"Ping: {target}", target=target)

    if _contains_any(lowered, ("ipconfig", "ip 구성", "어댑터 상세", "인터페이스 상세")):
        return NetOpsChatAction("ipconfig", "ipconfig /all")

    if _contains_any(lowered, ("route print", "라우팅 테이블", "라우트 테이블", "경로 테이블")):
        return NetOpsChatAction("route_print", "route print")

    if _contains_any(lowered, ("arp -a", "arp 테이블", "arp 목록")):
        return NetOpsChatAction("arp_table", "ARP 테이블")

    if _is_wireless_scan_request(lowered):
        duration_seconds, interval_seconds = _extract_scan_timing(text)
        return NetOpsChatAction(
            "wireless_scan",
            f"Wi-Fi 주변 AP 스캔 ({duration_seconds}초/{interval_seconds}초 간격)",
            duration_seconds=duration_seconds,
            interval_seconds=interval_seconds,
            risk_level="low",
            impact="Nearby Wi-Fi scanning refreshes adapter scan results and reads visible SSID/BSSID signal metadata.",
        )

    if _contains_any(lowered, ("wifi", "wi-fi", "wlan", "무선", "와이파이")):
        return NetOpsChatAction("wireless_status", "Wi-Fi 상태")

    if _contains_any(lowered, ("인터페이스", "어댑터", "adapter", "interface")):
        return NetOpsChatAction("interface_snapshot", "네트워크 인터페이스 상태")

    return None


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_external_ping_request(text: str) -> bool:
    has_ping_intent = _contains_any(text, ("ping", "핑", "핑테스트", "핑 테스트", "icmp"))
    has_external_scope = _contains_any(text, ("외부", "인터넷", "internet", "external", "outside", "wan", "공용 dns"))
    return has_ping_intent and has_external_scope


def _is_dns_flush_request(text: str) -> bool:
    return _contains_any(text, ("dns", "도메인")) and _contains_any(text, ("cache", "캐시")) and _contains_any(
        text,
        ("flush", "clear", "delete", "reset", "비워", "비우", "삭제", "초기화", "플러시"),
    )


def _is_oui_cache_refresh_request(text: str) -> bool:
    return _contains_any(text, ("oui", "vendor", "벤더")) and _contains_any(
        text,
        ("cache refresh", "refresh", "update", "캐시 갱신", "캐시 업데이트", "갱신", "업데이트"),
    )


def _is_wireless_scan_request(text: str) -> bool:
    has_wireless_scope = _contains_any(
        text,
        (
            "wifi",
            "wi-fi",
            "wlan",
            "wireless",
            "무선",
            "와이파이",
            "주변 ap",
            "access point",
            "bssid",
            "ssid",
        ),
    )
    has_scan_intent = _contains_any(
        text,
        (
            "scan",
            "scanner",
            "survey",
            "nearby",
            "around",
            "스캔",
            "검색",
            "점검",
            "상태 점검",
            "주변",
            "근처",
            "혼잡",
            "간섭",
            "채널",
            "탐색",
            "찾아",
            "찾기",
            "목록",
        ),
    )
    return has_wireless_scope and has_scan_intent


def _extract_scan_timing(text: str) -> tuple[int, int]:
    duration_seconds = _extract_duration_seconds(text, default=20)
    interval_seconds = _extract_interval_seconds(text, default=5)
    duration_seconds = max(5, min(duration_seconds, 120))
    interval_seconds = max(2, min(interval_seconds, 30))
    if interval_seconds > duration_seconds:
        interval_seconds = duration_seconds
    return duration_seconds, interval_seconds


def _extract_cidr(text: str) -> str:
    match = CIDR_RE.search(text)
    return match.group(0) if match else ""


def _extract_interval_seconds(text: str, *, default: int) -> int:
    interval_patterns = (
        r"(?i)(?:interval|every)\s*(?:of\s*)?((?:\d+(?:\.\d+)?\s*(?:sec(?:ond)?s?|s\b|min(?:ute)?s?|m\b)\s*){1,2})",
        r"((?:\d+(?:\.\d+)?\s*(?:초|분)\s*){1,2})(?:마다|간격)",
    )
    for pattern in interval_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        seconds = _duration_phrase_seconds(match.group(1))
        if seconds:
            return seconds
    return default


def _extract_duration_seconds(text: str, *, default: int) -> int:
    duration_patterns = (
        r"(?i)(?:for|during)\s+(.{1,40}?)(?:$|[,.;]|\s+(?:every|interval|scan|스캔))",
        r"(.{1,40}?)(?:동안|간)",
    )
    for pattern in duration_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        seconds = _duration_phrase_seconds(match.group(1))
        if seconds:
            return seconds

    seconds = _duration_phrase_seconds(text)
    return seconds or default


def _duration_phrase_seconds(text: str) -> int:
    value = str(text or "")
    total = 0
    matched = False
    for amount, unit in re.findall(
        r"(?i)(\d+(?:\.\d+)?)\s*(초|sec(?:ond)?s?|s\b|분|min(?:ute)?s?|m\b)",
        value,
    ):
        matched = True
        number = float(amount)
        unit_lower = unit.casefold()
        if unit in {"분"} or unit_lower.startswith("min") or unit_lower == "m":
            total += int(number * 60)
        else:
            total += int(number)
    return total if matched else 0


def _ping_alias_target(text: str) -> str:
    if not _contains_any(text, ("ping", "핑", "핑테스트", "핑 테스트", "icmp")):
        return ""
    if _contains_any(text, ("구글", "google")):
        return "google.com"
    if _contains_any(text, ("cloudflare", "클라우드플레어", "클플")):
        return "1.1.1.1"
    return ""


def _plan_network_change_action(text: str, lowered: str) -> NetOpsChatAction | None:
    if not _has_change_intent(lowered):
        return None

    interface_name = _extract_interface_name(text)
    if not interface_name:
        return None

    if "dhcp" in lowered:
        return NetOpsChatAction(
            "set_dhcp",
            f"IP 설정 변경: {interface_name} DHCP",
            interface_name=interface_name,
            requires_approval=True,
            risk_level="high",
            impact=(
                f"{interface_name} 인터페이스의 IPv4 주소와 DNS 서버를 DHCP로 전환합니다. "
                "적용 중 네트워크 연결이 일시적으로 끊길 수 있습니다."
            ),
            admin_required=True,
        )

    static_config = _extract_static_ip_config(text)
    if static_config is not None and _contains_any(lowered, ("ip", "ipv4", "고정", "static", "주소")):
        ip_address, prefix, gateway, static_dns_servers = static_config
        return NetOpsChatAction(
            "set_static_ip",
            f"고정 IP 설정: {interface_name} -> {ip_address}/{prefix}",
            interface_name=interface_name,
            ip_address=ip_address,
            prefix=prefix,
            gateway=gateway,
            dns_servers=tuple(static_dns_servers),
            requires_approval=True,
            risk_level="high",
            impact=(
                f"{interface_name} 인터페이스에 고정 IP {ip_address}/{prefix}"
                + (f", 게이트웨이 {gateway}" if gateway else "")
                + (f", DNS {', '.join(static_dns_servers)}" if static_dns_servers else "")
                + "를 적용합니다. "
                "값이 틀리면 현재 네트워크 연결이 끊기거나 원격 접속이 중단될 수 있습니다."
            ),
            admin_required=True,
        )

    dns_servers = _extract_dns_servers_from_text(text)
    if _contains_any(lowered, ("dns", "dns서버", "네임서버")) and dns_servers:
        return NetOpsChatAction(
            "set_dns",
            f"DNS 설정 변경: {interface_name} -> {', '.join(dns_servers)}",
            interface_name=interface_name,
            dns_servers=tuple(dns_servers),
            requires_approval=True,
            risk_level="medium",
            impact=(
                f"{interface_name} 인터페이스의 DNS 서버를 {', '.join(dns_servers)}로 변경합니다. "
                "잘못된 DNS를 적용하면 도메인 접속이 실패할 수 있습니다."
            ),
            admin_required=True,
        )

    return None


def _has_change_intent(text: str) -> bool:
    return _contains_any(text, ("변경", "설정", "적용", "바꿔", "바꾸", "전환", "수정", "change", "set", "apply"))


def _extract_interface_name(text: str) -> str:
    quoted = re.search(r"['\"“”]([^'\"“”]{1,80})['\"“”]", text)
    if quoted:
        return quoted.group(1).strip()

    keyword_match = re.search(
        r"(?i)(?:인터페이스|어댑터|adapter|interface)\s*[:=]?\s*"
        r"([A-Za-z0-9가-힣_.\- ]{1,60}?)(?=\s+(?:dhcp|dns|ip|ipv4|고정|static|주소|를|을|로|으로|변경|설정|적용|바꿔)|[,.;]|$)",
        text,
    )
    if keyword_match:
        return keyword_match.group(1).strip()

    common_match = re.search(r"(?i)(?<![A-Za-z0-9_-])(Ethernet|Wi-?Fi|WLAN|이더넷)(?![A-Za-z0-9_-])", text)
    return common_match.group(1).strip() if common_match else ""


def _extract_dns_servers_from_text(text: str) -> list[str]:
    dns_match = re.search(r"(?i)(?:dns|dns서버|네임서버)\s*[:=]?\s*(.+)$", text)
    if not dns_match:
        return []
    return _valid_ipv4_values(IPV4_RE.findall(dns_match.group(1)))


def _extract_static_ip_config(text: str) -> tuple[str, int, str, list[str]] | None:
    addresses = _valid_ipv4_values(IPV4_RE.findall(text))
    if not addresses:
        return None
    ip_address = addresses[0]
    prefix = _extract_prefix_for_ip(text, ip_address)
    if not prefix:
        return None
    gateway_match = re.search(rf"(?i)(?:gateway|gw|게이트웨이)\s*[:=]?\s*({IPV4_RE.pattern})", text)
    gateway = gateway_match.group(1) if gateway_match and _is_ipv4_address(gateway_match.group(1)) else ""
    dns_servers = _extract_dns_servers_from_text(text)
    return ip_address, prefix, gateway, dns_servers


def _extract_prefix_for_ip(text: str, ip_address: str) -> int:
    ip_prefix = re.search(rf"{re.escape(ip_address)}\s*/\s*(\d{{1,2}})", text)
    if ip_prefix:
        prefix = int(ip_prefix.group(1))
        return prefix if 1 <= prefix <= 32 else 0
    prefix_match = re.search(r"(?i)(?:prefix|프리픽스|서브넷|mask|마스크)\s*[:=]?\s*(\d{1,2})", text)
    if prefix_match:
        prefix = int(prefix_match.group(1))
        return prefix if 1 <= prefix <= 32 else 0
    return 0


def _valid_ipv4_values(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if _is_ipv4_address(value) and value not in unique:
            unique.append(value)
    return unique


def _extract_netops_target(text: str) -> str:
    ip_match = IPV4_RE.search(text)
    if ip_match and _is_ipv4_address(ip_match.group(0)):
        return ip_match.group(0)
    domain_match = DOMAIN_RE.search(text)
    if domain_match:
        return domain_match.group(0).rstrip(".,;")
    if re.search(r"(?i)(?<![A-Za-z0-9_-])localhost(?![A-Za-z0-9_-])", text):
        return "localhost"
    return ""


def _extract_netops_port(text: str) -> int:
    patterns = (
        r":\s*(\d{1,5})(?!\d)",
        r"(?i)\bport\s*[:=]?\s*(\d{1,5})\b",
        r"포트\s*[:=]?\s*(\d{1,5})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        port = int(match.group(1))
        if 1 <= port <= 65535:
            return port
    for raw_port in re.findall(r"(?<![\d.])(\d{1,5})(?![\d.])", text):
        port = int(raw_port)
        if 1 <= port <= 65535:
            return port
    return 0


def _extract_netops_mac(text: str) -> str:
    match = MAC_RE.search(text)
    return match.group(0).strip() if match else ""


def _extract_dns_record_type(text: str) -> str:
    upper_text = text.upper()
    for record_type in DNS_RECORD_TYPES:
        if re.search(rf"(?<![A-Z0-9]){record_type}(?![A-Z0-9])", upper_text):
            return record_type
    return "A"


def _is_ipv4_address(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _no_resolve_requested(text: str) -> bool:
    return _contains_any(text, ("-d", "-n", "resolve 안", "이름 해석 안", "역방향 조회 안"))


def diagnose_cli_error(provider_key: str, detail: str) -> str:
    text = detail.strip()
    if not text:
        return ""
    structured_error = extract_error_from_cli_line(text)
    diagnostic_text = structured_error or text
    if provider_key == "codex" and _is_codex_model_requires_newer_version_error(diagnostic_text):
        model = _extract_codex_incompatible_model(diagnostic_text)
        selected = f" '{model}'" if model else ""
        return (
            f"현재 선택한 모델{selected}은(는) 실행 중인 Codex CLI보다 새 버전이 필요합니다.\n\n"
            "저장된 모델 선택은 변경하지 않았습니다. 연결 설정에서 '모델 목록 새로고침'을 누른 뒤 "
            "현재 목록에 표시되는 지원 모델 또는 '자동 선택'을 사용하세요. "
            "이 모델을 계속 사용하려면 Codex CLI를 업데이트한 뒤 모델 목록을 다시 새로고침하세요.\n\n"
            f"원본 오류:\n{diagnostic_text}"
        )
    if provider_key == "codex" and _is_codex_reasoning_effort_config_error(text):
        unknown_variant = _extract_unknown_variant(text)
        config_path = _extract_codex_config_path(text)
        location = f"\n설정 파일: {config_path}" if config_path else ""
        return (
            "Codex CLI 설정 파일을 읽지 못했습니다."
            f"{location}\n\n"
            '현재 설치된 Codex CLI의 model_reasoning_effort는 "none", "minimal", "low", '
            '"medium", "high", "xhigh"만 지원합니다. '
            'config.toml의 호환되지 않는 값은 model_reasoning_effort = "xhigh"로 '
            "자동 복구할 수 있습니다.\n\n"
            "이 값은 NetOps Suite의 모델별 추론 설정과는 별개인 Codex 전역 설정입니다.\n\n"
            f"원본 오류:\n{text}"
        )
    if provider_key == "codex" and _is_codex_service_tier_config_error(text):
        unknown_variant = _extract_unknown_variant(text)
        if unknown_variant in {"default", "priority"}:
            return (
                f'현재 실행 중인 Codex CLI가 service_tier = "{unknown_variant}" 값을 지원하지 않습니다. '
                "최신 Codex에서 사용하는 설정일 수 있어 자동 변경하지 않고 그대로 보존합니다. "
                "최신 Codex CLI로 업데이트한 뒤 다시 시도하세요.\n\n"
                f"원본 오류:\n{text}"
            )
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
    return provider_key == "codex" and (
        _is_codex_reasoning_effort_config_error(text) or _is_codex_service_tier_config_error(text)
    )


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
    replacement_reasoning_effort: str = "xhigh",
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

    service_tier_pattern = re.compile(
        r"(?m)^(\s*service_tier\s*=\s*)([\"'])([^\"']+)([\"'])(\s*(?:#.*)?)$"
    )
    reasoning_effort_pattern = re.compile(
        r"(?m)^(\s*model_reasoning_effort\s*=\s*)([\"'])([^\"']+)([\"'])(\s*(?:#.*)?)$"
    )
    # This repair follows the blocking parser error returned by the executable
    # that is actually being launched. Unsupported values must be replaced so
    # status, login, and model discovery can run again.
    valid_service_tiers = {"default", "priority", "fast", "flex"}
    valid_reasoning_efforts = {"none", "minimal", "low", "medium", "high", "xhigh"}
    service_tier_match = service_tier_pattern.search(original)
    reasoning_effort_match = reasoning_effort_pattern.search(original)
    changes: list[tuple[str, str, str]] = []

    def replace_invalid(
        match: re.Match[str],
        *,
        setting_name: str,
        valid_values: set[str],
        replacement: str,
    ) -> str:
        previous = match.group(3).strip()
        if previous in valid_values:
            return match.group(0)
        changes.append((setting_name, previous, replacement))
        return f'{match.group(1)}"{replacement}"{match.group(5)}'

    repaired = reasoning_effort_pattern.sub(
        lambda item: replace_invalid(
            item,
            setting_name="model_reasoning_effort",
            valid_values=valid_reasoning_efforts,
            replacement=replacement_reasoning_effort,
        ),
        original,
    )
    repaired = service_tier_pattern.sub(
        lambda item: replace_invalid(
            item,
            setting_name="service_tier",
            valid_values=valid_service_tiers,
            replacement=replacement_service_tier,
        ),
        repaired,
    )
    if not changes:
        unknown_variant = _extract_unknown_variant(detail)
        if unknown_variant in valid_reasoning_efforts | valid_service_tiers:
            return CliConfigurationRepairResult(
                attempted=True,
                repaired=False,
                config_path=str(config_path),
                message=(
                    f'현재 Codex CLI가 "{unknown_variant}" 값을 거부했지만 최신 버전에서 사용하는 '
                    "설정일 수 있어 자동 변경하지 않았습니다. 최신 Codex CLI로 업데이트하세요."
                ),
            )
        if (
            _is_codex_service_tier_config_error(detail)
            and service_tier_match is not None
            and service_tier_match.group(3).strip() in valid_service_tiers
        ):
            previous_value = service_tier_match.group(3).strip()
            return CliConfigurationRepairResult(
                attempted=True,
                repaired=False,
                config_path=str(config_path),
                message=f"Codex service_tier는 이미 호환되는 값입니다: {previous_value}",
            )
        if (
            _is_codex_reasoning_effort_config_error(detail)
            and reasoning_effort_match is not None
            and reasoning_effort_match.group(3).strip() in valid_reasoning_efforts
        ):
            previous_value = reasoning_effort_match.group(3).strip()
            return CliConfigurationRepairResult(
                attempted=True,
                repaired=False,
                config_path=str(config_path),
                message=f"Codex model_reasoning_effort는 이미 호환되는 값입니다: {previous_value}",
            )
        return CliConfigurationRepairResult(
            attempted=True,
            repaired=False,
            config_path=str(config_path),
            message=(
                "Codex 설정 파일에서 자동 복구할 수 있는 "
                "model_reasoning_effort 또는 service_tier 항목을 찾지 못했습니다.\n"
                f"설정 파일: {config_path}"
            ),
        )

    backup_path = config_path.with_name(
        f"{config_path.name}.bak-netops-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    )

    try:
        backup_path.write_text(original, encoding="utf-8")
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=str(config_path.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
                temp_file.write(repaired)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, config_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
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
            + "\n".join(
                f'{name} = "{previous}" -> {name} = "{replacement}"'
                for name, previous, replacement in changes
            )
        ),
    )


def _is_codex_service_tier_config_error(detail: str) -> bool:
    lowered = detail.casefold()
    return (
        "error loading configuration" in lowered
        and "unknown variant" in lowered
        and ("service_tier" in lowered or ("expected" in lowered and "fast" in lowered and "flex" in lowered))
    )


def _is_codex_reasoning_effort_config_error(detail: str) -> bool:
    lowered = detail.casefold()
    expected_values = ("none", "minimal", "low", "medium", "high", "xhigh")
    return (
        "error loading configuration" in lowered
        and "unknown variant" in lowered
        and (
            "model_reasoning_effort" in lowered
            or ("expected" in lowered and all(value in lowered for value in expected_values))
        )
    )


def _is_codex_model_requires_newer_version_error(detail: str) -> bool:
    return "requires a newer version of codex" in detail.casefold()


def _extract_codex_incompatible_model(detail: str) -> str:
    match = re.search(
        r"(?:the\s+)?[\"'`](?P<model>[A-Za-z0-9][A-Za-z0-9._:/-]{0,127})[\"'`]\s+model\s+requires\s+a\s+newer\s+version\s+of\s+codex",
        detail,
        re.IGNORECASE,
    )
    return match.group("model") if match else ""


def _extract_codex_config_path(detail: str) -> str:
    match = re.search(r"Error loading configuration:\s*(.+?config\.toml)(?::\d+:\d+)?", detail, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_unknown_variant(detail: str) -> str:
    match = re.search(r"unknown variant\s+[`'\"]?([^`'\"\s,]+)", detail, re.IGNORECASE)
    return match.group(1).strip().casefold() if match else ""


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
        global_args=CODEX_NONINTERACTIVE_GLOBAL_ARGS,
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


# Static choices are a recovery path only. Live provider catalogs drive the normal model picker.
FALLBACK_MODEL_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "codex": (
        ("자동 선택 (권장)", ""),
    ),
    "claude": (
        ("자동 선택 (권장)", ""),
        ("Fable 자동 별칭", "fable"),
        ("Opus 자동 별칭", "opus"),
        ("Sonnet 자동 별칭", "sonnet"),
        ("Claude Opus 4.8", "claude-opus-4-8"),
        ("Claude Sonnet 5", "claude-sonnet-5"),
        ("Claude Haiku 4.5", "claude-haiku-4-5"),
    ),
    "gemini": (
        ("자동 선택 (권장)", ""),
        ("Gemini 3 Pro 미리보기", "gemini-3-pro-preview"),
        ("Gemini 3 Flash 미리보기", "gemini-3-flash-preview"),
        ("Gemini Flash 최신", "gemini-flash-latest"),
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
    options = list(FALLBACK_MODEL_OPTIONS.get(key, (("자동 선택 (권장)", ""),)))
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
        if _is_windowsapps_alias(expanded):
            for candidate in _provider_program_candidates(spec):
                if candidate:
                    return candidate
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
        if spec.key == "codex":
            candidates.extend(
                [
                    str(Path(appdata) / "npm" / "codex.cmd") if appdata else "",
                    shutil.which("codex.cmd") or "",
                ]
            )
            if local_appdata:
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
    error_message = extract_error_from_cli_line(stripped)
    if error_message:
        return error_message
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    found = _collect_text_fragments(payload)
    return sanitize_cli_text("".join(found)).strip()


def extract_error_from_cli_line(line: str) -> str:
    """Return only the user-safe message from a structured CLI error event."""
    stripped = sanitize_cli_text(line).strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict) or str(payload.get("type", "")).casefold() != "error":
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message", "")
    elif isinstance(error, str):
        try:
            decoded_error = json.loads(error)
        except json.JSONDecodeError:
            message = error
        else:
            message = decoded_error.get("message", "") if isinstance(decoded_error, dict) else ""
    else:
        message = payload.get("message", "")
    return sanitize_cli_text(message).strip() if isinstance(message, str) else ""


def is_codex_model_cache_compatibility_warning(detail: str) -> bool:
    """Identify the old-CLI/new-model-cache warning without translating its enum value."""
    lowered = sanitize_cli_text(detail).casefold()
    cache_source = (
        "codex_models_manager::cache" in lowered
        or "codex_models::manager::cache" in lowered
        or "failed to load models cache" in lowered
    )
    return bool(
        cache_source
        and "unknown variant" in lowered
        and re.search(r"unknown variant\s+[`'\"]?max(?:[`'\"]|\b)", lowered)
    )


def split_codex_model_cache_warning(detail: str) -> tuple[str, str]:
    """Split transient Codex model-cache warnings from the actionable CLI error text."""
    regular_lines: list[str] = []
    cache_warning_lines: list[str] = []
    for line in sanitize_cli_text(detail).splitlines():
        target = cache_warning_lines if is_codex_model_cache_compatibility_warning(line) else regular_lines
        target.append(line)
    return "\n".join(regular_lines).strip(), "\n".join(cache_warning_lines).strip()


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
