from __future__ import annotations

import json
import locale
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.ai_models import AiProviderConfig, CliInvocation
from app.services.logging_service import redact_log_text


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
CIDR_RE = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}/(?:[0-9]|[12][0-9]|3[0-2])(?![\d.])"
)
DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![A-Za-z0-9_-])"
)
MAC_RE = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}(?:[:\-\.\s][0-9a-f]{2}){2,7}|[0-9a-f]{6,12})(?![0-9a-f])"
)
RESERVED_EXTRA_ARG_FLAGS = {
    "-a",
    "-s",
    "-C",
    "-r",
    "--help",
    "--version",
    "--ask-for-approval",
    "--sandbox",
    "--cd",
    "--add-dir",
    "--model",
    "--image",
    "--output-format",
    "--json",
    "--verbose",
    "--continue",
    "--resume",
    "--last",
    "--session-id",
    "--fork-session",
    "--full-auto",
    "--skip-git-repo-check",
    "--dangerously-bypass-approvals-and-sandbox",
}
CODEX_NONINTERACTIVE_GLOBAL_ARGS = ("-a", "never", "-s", "read-only")
CODEX_SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})
DNS_RECORD_TYPES = ("AAAA", "CNAME", "PTR", "TXT", "MX", "NS", "A")
DEFAULT_EXTERNAL_PING_TARGETS = ("8.8.8.8", "1.1.1.1", "google.com")
MAX_PROVIDER_DIAGNOSTIC_CHARS = 4000

PROVIDER_EVENT_FINAL_TEXT = "final_text"
PROVIDER_EVENT_PROGRESS = "progress"
PROVIDER_EVENT_TOOL = "tool"
PROVIDER_EVENT_REASONING = "reasoning"
PROVIDER_EVENT_ERROR = "error"
PROVIDER_EVENT_SESSION = "session"

NETOPS_ASSISTANT_ROLE_PROMPT = """당신은 NetOps Suite 안에서 동작하는 사용자 도우미입니다.
검증된 NetOps 내부 기능 계약과 번들 사용자 가이드를 외부 스크립트보다 먼저 사용하세요.
사용자에게 보이는 공개 화면명만 사용하고 내부 enum, 소스 경로, 존재가 확인되지 않은 버튼을 노출하거나 추측하지 마세요.
기능이 부분 지원이면 가능한 내부 절차와 한계를 먼저 설명하고, 이번 요청의 명시적 동의 전에는 스크립트나 외부 절차를 작성하지 마세요.
파일 검색, 도구 호출, 내부 추론과 진행 과정을 답변으로 내보내지 말고 최종 사용자 답변만 작성하세요."""


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """A provider-neutral CLI event safe for UI routing.

    ``text`` is intentionally separated from ``kind`` so callers can keep
    reasoning, tool output, and progress details out of chat transcripts.
    """

    provider_key: str
    kind: str
    text: str = ""
    session_id: str = ""
    raw_type: str = ""
    turn_complete: bool = False


@dataclass(slots=True)
class ProviderEventAccumulator:
    """Buffer assistant candidates until a provider turn is complete.

    Codex may emit more than one completed ``agent_message`` item while it is
    working. Only the last candidate is returned when ``turn.completed`` is
    observed (or when :meth:`finish` is called after process exit).
    """

    provider_key: str
    session_id: str = ""
    error_text: str = ""
    diagnostic_text: str = ""
    _candidate: str = ""

    def feed(self, event: ProviderEvent | None) -> str:
        if event is None:
            return ""

        if event.kind == PROVIDER_EVENT_SESSION and event.session_id:
            self.session_id = event.session_id
        elif event.kind == PROVIDER_EVENT_ERROR and event.text:
            self.error_text = event.text
            self._candidate = ""
            return ""
        elif event.kind == PROVIDER_EVENT_FINAL_TEXT and event.text:
            self._candidate = event.text

        if event.turn_complete:
            return self.finish()
        return ""

    def feed_line(self, line: str) -> str:
        event = parse_provider_event(line)
        if (
            event is not None
            and event.kind == PROVIDER_EVENT_PROGRESS
            and event.raw_type == "plain"
        ):
            diagnostic = redact_log_text(sanitize_cli_text(line)).strip()
            if diagnostic:
                combined = "\n".join(
                    part for part in (self.diagnostic_text, diagnostic) if part
                )
                self.diagnostic_text = combined[-MAX_PROVIDER_DIAGNOSTIC_CHARS:]
        return self.feed(event)

    def finish(self) -> str:
        completed = self._candidate.strip()
        self._candidate = ""
        return completed


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
    ports: tuple[int, ...] = ()
    endpoints: tuple[tuple[str, int], ...] = ()
    count: int = 0
    timeout_ms: int = 0
    continuous: bool = False
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
    targets = _extract_netops_targets(text)
    target = targets[0] if targets else ""
    ports = _extract_netops_ports(text)
    port = ports[0] if ports else 0
    probe_count = _extract_probe_count(text)
    probe_timeout_ms = _extract_probe_timeout_ms(text)
    continuous = _is_continuous_probe_request(lowered)
    mac_addresses = _extract_netops_macs(text)
    mac_address = mac_addresses[0] if mac_addresses else ""

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

    cidrs = _extract_cidrs(text)
    cidr = cidrs[0] if cidrs else ""
    if cidr and _contains_any(
        lowered, ("subnet", "cidr", "서브넷", "대역", "network calculate", "계산")
    ):
        return NetOpsChatAction(
            "subnet_calculate",
            _multi_item_title("Subnet calculate", cidrs),
            target=cidr,
            targets=tuple(cidrs),
        )

    if _contains_any(lowered, ("oui", "vendor", "벤더", "제조사")) and mac_address:
        return NetOpsChatAction(
            "oui_lookup",
            _multi_item_title("OUI 제조사 조회", mac_addresses),
            target=mac_address,
            targets=tuple(mac_addresses),
        )

    if _contains_any(
        lowered,
        (
            "공인 ip",
            "공인아이피",
            "외부 ip",
            "public ip",
            "external ip",
            "what is my ip",
            "내 공인",
        ),
    ):
        return NetOpsChatAction("public_ip", "공인 IP 확인")

    if _has_change_intent(lowered) and len(_extract_interface_names(text)) > 1:
        return None
    change_action = _plan_network_change_action(text, lowered)
    if change_action is not None:
        return change_action

    ping_alias_target = _ping_alias_target(lowered) if not targets else ""
    if ping_alias_target:
        return NetOpsChatAction(
            "ping_batch" if continuous else "ping",
            f"Ping: {ping_alias_target}",
            target=ping_alias_target,
            targets=(ping_alias_target,),
            count=probe_count,
            timeout_ms=probe_timeout_ms,
            continuous=continuous,
        )

    if _is_external_ping_request(lowered):
        external_targets = tuple(targets or DEFAULT_EXTERNAL_PING_TARGETS)
        return NetOpsChatAction(
            "external_ping",
            _multi_item_title("외부 Ping 테스트", external_targets),
            target=external_targets[0],
            targets=external_targets,
            count=probe_count,
            timeout_ms=probe_timeout_ms,
            continuous=continuous,
        )

    if (
        _contains_any(
            lowered,
            ("tcping", "tcp", "포트", "port", "열려", "열렸", "접속", "연결 확인"),
        )
        and target
        and port
    ):
        endpoints = _extract_tcp_endpoints(text, targets)
        if _has_ambiguous_tcp_endpoint_request(text, targets, ports, endpoints):
            return None
        if len(endpoints) > 1:
            if continuous:
                return None
            return NetOpsChatAction(
                "tcp_check",
                "TCP 포트 확인: "
                + ", ".join(
                    f"{host}:{endpoint_port}" for host, endpoint_port in endpoints
                ),
                target=endpoints[0][0],
                port=endpoints[0][1],
                targets=tuple(host for host, _endpoint_port in endpoints),
                ports=tuple(endpoint_port for _host, endpoint_port in endpoints),
                endpoints=tuple(endpoints),
                count=probe_count,
                timeout_ms=probe_timeout_ms,
            )
        if len(targets) > 1 or len(ports) > 1:
            return NetOpsChatAction(
                "tcp_batch",
                f"TCP 포트 확인: {', '.join(targets)} / {', '.join(str(value) for value in ports)}",
                target=target,
                port=port,
                targets=tuple(targets),
                ports=tuple(ports),
                count=probe_count,
                timeout_ms=probe_timeout_ms,
                continuous=continuous,
            )
        return NetOpsChatAction(
            "tcp_check",
            f"TCP 포트 확인: {target}:{port}",
            target=target,
            port=port,
            targets=tuple(targets),
            ports=tuple(ports),
            count=probe_count,
            timeout_ms=probe_timeout_ms,
            continuous=continuous,
        )

    if (
        _contains_any(lowered, ("dns", "nslookup", "도메인 조회", "레코드 조회"))
        and target
    ):
        server = _extract_dns_server(text)
        query_targets = [
            item
            for item in targets
            if not server or item.casefold() != server.casefold()
        ]
        if not query_targets:
            return None
        target = query_targets[0]
        record_type = _extract_dns_record_type(text)
        if (
            record_type == "A"
            and _is_ipv4_address(target)
            and _contains_any(lowered, ("ptr", "reverse", "역방향"))
        ):
            record_type = "PTR"
        return NetOpsChatAction(
            "dns_lookup",
            f"DNS 조회 ({record_type}): {', '.join(query_targets)}",
            target=target,
            targets=tuple(query_targets),
            record_type=record_type,
            server=server,
        )

    if _contains_any(lowered, ("pathping", "패스핑")) and target:
        return NetOpsChatAction(
            "pathping",
            _multi_item_title("pathping", targets),
            target=target,
            targets=tuple(targets),
            resolve_names=not _no_resolve_requested(lowered),
        )

    if (
        _contains_any(
            lowered, ("tracert", "traceroute", "trace route", "경로 추적", "홉 추적")
        )
        and target
    ):
        return NetOpsChatAction(
            "tracert",
            _multi_item_title("tracert", targets),
            target=target,
            targets=tuple(targets),
            resolve_names=not _no_resolve_requested(lowered),
        )

    if _contains_any(lowered, ("ping", "핑")) and target:
        if len(targets) > 1 or continuous:
            return NetOpsChatAction(
                "ping_batch",
                _multi_item_title("Ping", targets),
                target=target,
                targets=tuple(targets),
                count=probe_count,
                timeout_ms=probe_timeout_ms,
                continuous=continuous,
            )
        return NetOpsChatAction(
            "ping",
            f"Ping: {target}",
            target=target,
            targets=tuple(targets),
            count=probe_count,
            timeout_ms=probe_timeout_ms,
        )

    if _contains_any(
        lowered, ("ipconfig", "ip 구성", "어댑터 상세", "인터페이스 상세")
    ):
        return NetOpsChatAction("ipconfig", "ipconfig /all")

    if _contains_any(
        lowered, ("route print", "라우팅 테이블", "라우트 테이블", "경로 테이블")
    ):
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
    has_ping_intent = _contains_any(
        text, ("ping", "핑", "핑테스트", "핑 테스트", "icmp")
    )
    has_external_scope = _contains_any(
        text, ("외부", "인터넷", "internet", "external", "outside", "wan", "공용 dns")
    )
    return has_ping_intent and has_external_scope


def _is_dns_flush_request(text: str) -> bool:
    return (
        _contains_any(text, ("dns", "도메인"))
        and _contains_any(text, ("cache", "캐시"))
        and _contains_any(
            text,
            (
                "flush",
                "clear",
                "delete",
                "reset",
                "비워",
                "비우",
                "삭제",
                "초기화",
                "플러시",
            ),
        )
    )


def _is_oui_cache_refresh_request(text: str) -> bool:
    return _contains_any(text, ("oui", "vendor", "벤더")) and _contains_any(
        text,
        (
            "cache refresh",
            "refresh",
            "update",
            "캐시 갱신",
            "캐시 업데이트",
            "갱신",
            "업데이트",
        ),
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


def _extract_cidrs(text: str) -> list[str]:
    return _ordered_unique(match.group(0) for match in CIDR_RE.finditer(text))


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

    interface_names = _extract_interface_names(text)
    if len(interface_names) != 1:
        return None
    interface_name = interface_names[0]

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
    if static_config is not None and _contains_any(
        lowered, ("ip", "ipv4", "고정", "static", "주소")
    ):
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
                + (
                    f", DNS {', '.join(static_dns_servers)}"
                    if static_dns_servers
                    else ""
                )
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
    return _contains_any(
        text,
        (
            "변경",
            "설정",
            "적용",
            "바꿔",
            "바꾸",
            "전환",
            "수정",
            "change",
            "set",
            "apply",
        ),
    )


def _extract_interface_name(text: str) -> str:
    interface_names = _extract_interface_names(text)
    return interface_names[0] if interface_names else ""


def _extract_interface_names(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for quoted in re.finditer(r"['\"“”]([^'\"“”]{1,80})['\"“”]", text):
        matches.append((quoted.start(), quoted.group(1).strip()))

    keyword_pattern = (
        r"(?i)(?:인터페이스|어댑터|adapter|interface)\s*[:=]?\s*"
        r"([A-Za-z0-9가-힣_.\- ]{1,60}?)(?=\s+(?:dhcp|dns|ip|ipv4|고정|static|주소|를|을|로|으로|변경|설정|적용|바꿔)|[,.;]|$)"
    )
    for keyword_match in re.finditer(keyword_pattern, text):
        matches.append((keyword_match.start(), keyword_match.group(1).strip()))

    for common_match in re.finditer(
        r"(?i)(?<![A-Za-z0-9_-])(Ethernet|Wi-?Fi|WLAN|이더넷)(?![A-Za-z0-9_-])", text
    ):
        matches.append((common_match.start(), common_match.group(1).strip()))
    matches.sort(key=lambda item: item[0])
    return _ordered_unique(value for _position, value in matches)


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
    gateway_match = re.search(
        rf"(?i)(?:gateway|gw|게이트웨이)\s*[:=]?\s*({IPV4_RE.pattern})", text
    )
    gateway = (
        gateway_match.group(1)
        if gateway_match and _is_ipv4_address(gateway_match.group(1))
        else ""
    )
    dns_servers = _extract_dns_servers_from_text(text)
    return ip_address, prefix, gateway, dns_servers


def _extract_prefix_for_ip(text: str, ip_address: str) -> int:
    ip_prefix = re.search(rf"{re.escape(ip_address)}\s*/\s*(\d{{1,2}})", text)
    if ip_prefix:
        prefix = int(ip_prefix.group(1))
        return prefix if 1 <= prefix <= 32 else 0
    prefix_match = re.search(
        r"(?i)(?:prefix|프리픽스|서브넷|mask|마스크)\s*[:=]?\s*(\d{1,2})", text
    )
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


def _extract_netops_targets(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for match in IPV4_RE.finditer(text):
        value = match.group(0)
        if _is_ipv4_address(value):
            matches.append((match.start(), value))
    for match in DOMAIN_RE.finditer(text):
        matches.append((match.start(), match.group(0).rstrip(".,;")))
    for match in re.finditer(r"(?i)(?<![A-Za-z0-9_-])localhost(?![A-Za-z0-9_-])", text):
        matches.append((match.start(), "localhost"))
    matches.sort(key=lambda item: item[0])
    return _ordered_unique(value for _position, value in matches)


def _extract_netops_target(text: str) -> str:
    targets = _extract_netops_targets(text)
    return targets[0] if targets else ""


def _extract_netops_ports(text: str) -> list[int]:
    ports: list[int] = []
    masked_modifiers = _mask_probe_modifiers(text)
    patterns = (
        r":\s*(\d{1,5})(?!\d)",
        r"(?i)\bport\s*[:=]?\s*(\d{1,5})\b",
        r"포트\s*[:=]?\s*(\d{1,5})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, masked_modifiers):
            port = int(match.group(1))
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)

    masked = IPV4_RE.sub(" ", CIDR_RE.sub(" ", masked_modifiers))
    range_spans: list[tuple[int, int]] = []
    for range_match in re.finditer(
        r"(?<![A-Za-z0-9.])(\d{1,5})\s*-\s*(\d{1,5})(?![A-Za-z0-9.])",
        masked,
    ):
        first = int(range_match.group(1))
        last = int(range_match.group(2))
        if not (1 <= first <= last <= 65535) or last - first + 1 > 64:
            return []
        range_spans.append(range_match.span())
        for value in range(first, last + 1):
            if value not in ports:
                ports.append(value)
    for start, end in reversed(range_spans):
        masked = masked[:start] + (" " * (end - start)) + masked[end:]
    for raw_port in re.findall(r"(?<![A-Za-z0-9.])(\d{1,5})(?![A-Za-z0-9.])", masked):
        port = int(raw_port)
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports


def _extract_netops_port(text: str) -> int:
    ports = _extract_netops_ports(text)
    return ports[0] if ports else 0


def _extract_netops_macs(text: str) -> list[str]:
    return _ordered_unique(match.group(0).strip() for match in MAC_RE.finditer(text))


def _extract_netops_mac(text: str) -> str:
    macs = _extract_netops_macs(text)
    return macs[0] if macs else ""


def _extract_tcp_endpoints(text: str, targets: list[str]) -> list[tuple[str, int]]:
    endpoints: list[tuple[str, int]] = []
    target_segments = 0
    paired_segments = 0
    for segment in re.split(r"[,;\r\n]+", text):
        segment_targets = _extract_netops_targets(segment)
        if not segment_targets:
            continue
        target_segments += 1
        segment_ports = _extract_netops_ports(segment)
        if len(segment_targets) != 1 or len(segment_ports) != 1:
            continue
        paired_segments += 1
        endpoint = (segment_targets[0], segment_ports[0])
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    if target_segments >= 2 and paired_segments == target_segments:
        return endpoints

    positioned: list[tuple[int, str, int]] = []
    for target in targets:
        for match in re.finditer(rf"{re.escape(target)}\s*:\s*(\d{{1,5}})", text):
            port = int(match.group(1))
            if 1 <= port <= 65535:
                positioned.append((match.start(), target, port))
    positioned.sort(key=lambda item: item[0])
    return _ordered_unique(
        (target, endpoint_port) for _position, target, endpoint_port in positioned
    )


def _has_ambiguous_tcp_endpoint_request(
    text: str,
    targets: list[str],
    ports: list[int],
    endpoints: list[tuple[str, int]],
) -> bool:
    if not endpoints:
        return False
    endpoint_targets = {target.casefold() for target, _port in endpoints}
    endpoint_ports = {port for _target, port in endpoints}
    has_colon_pair = any(
        re.search(rf"{re.escape(target)}\s*:\s*\d{{1,5}}", text) for target in targets
    )
    if set(ports).difference(endpoint_ports):
        return True
    return (
        has_colon_pair
        and len(targets) > 1
        and endpoint_targets != {target.casefold() for target in targets}
    )


def _extract_probe_count(text: str) -> int:
    patterns = (
        r"(?i)(?:count|-n)\s*[:=]?\s*(\d+)",
        r"(\d+)\s*(?:회|번)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 0


def _extract_probe_timeout_ms(text: str) -> int:
    patterns = (
        r"(?i)(?:timeout|time\s*out|제한\s*시간|타임아웃)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(ms|msec|milliseconds?|초|sec(?:ond)?s?|s\b)",
        r"(?i)(\d+(?:\.\d+)?)\s*(ms|msec|milliseconds?)\s*(?:timeout|제한\s*시간|타임아웃)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        amount = float(match.group(1))
        unit = match.group(2).casefold()
        multiplier = 1000 if unit in {"초", "s"} or unit.startswith("sec") else 1
        return max(1, int(amount * multiplier))
    return 0


def _is_continuous_probe_request(text: str) -> bool:
    return _contains_any(
        text,
        (
            "계속",
            "연속",
            "지속",
            "중지할 때까지",
            "until stopped",
            "continuous",
            "continuously",
            " -t",
        ),
    )


def _mask_probe_modifiers(text: str) -> str:
    masked = text
    patterns = (
        r"(?i)(?:count|-n)\s*[:=]?\s*\d+",
        r"\d+\s*(?:회|번)",
        r"(?i)(?:timeout|time\s*out|제한\s*시간|타임아웃)\s*[:=]?\s*\d+(?:\.\d+)?\s*(?:ms|msec|milliseconds?|초|sec(?:ond)?s?|s\b)",
        r"(?i)\d+(?:\.\d+)?\s*(?:ms|msec|milliseconds?|초|sec(?:ond)?s?|s\b)\s*(?:timeout|제한\s*시간|타임아웃)?",
    )
    for pattern in patterns:
        masked = re.sub(pattern, " ", masked)
    return masked


def _extract_dns_server(text: str) -> str:
    target_pattern = rf"(?:{IPV4_RE.pattern}|{DOMAIN_RE.pattern}|localhost)"
    patterns = (
        rf"(?i)(?P<server>{target_pattern})\s*(?:dns\s*)?서버(?:로|에서)?",
        rf"(?i)(?:server|서버)\s*[:=]?\s*(?P<server>{target_pattern})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group("server").rstrip(".,;")
            if (
                _is_ipv4_address(value)
                or DOMAIN_RE.fullmatch(value)
                or value.casefold() == "localhost"
            ):
                return value
    return ""


def _ordered_unique(values) -> list:
    unique: list = []
    seen: set[object] = set()
    for value in values:
        key = value.casefold() if isinstance(value, str) else value
        if not value or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _multi_item_title(prefix: str, values) -> str:
    items = [str(value) for value in values if str(value)]
    return f"{prefix}: {', '.join(items)}" if items else prefix


def _extract_dns_record_type(text: str) -> str:
    upper_text = text.upper()
    for record_type in DNS_RECORD_TYPES:
        if re.search(rf"(?<![A-Z0-9]){record_type}(?![A-Z0-9])", upper_text):
            return record_type
    return "A"


def _is_ipv4_address(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(
        part.isdigit() and 0 <= int(part) <= 255 for part in parts
    )


def _no_resolve_requested(text: str) -> bool:
    return _contains_any(
        text, ("-d", "-n", "resolve 안", "이름 해석 안", "역방향 조회 안")
    )


def diagnose_cli_error(provider_key: str, detail: str) -> str:
    text = detail.strip()
    if not text:
        return ""
    structured_error = extract_error_from_cli_line(text)
    diagnostic_text = structured_error or text
    if provider_key == "codex" and _is_codex_model_requires_newer_version_error(
        diagnostic_text
    ):
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


def is_authentication_cli_error(provider_key: str, detail: str) -> bool:
    """Return whether a provider failure means its official CLI must log in again."""

    if provider_key not in PROVIDER_SPECS:
        return False
    text = (extract_error_from_cli_line(detail) or detail).strip().casefold()
    if not text:
        return False
    common_markers = (
        "not logged in",
        "not authenticated",
        "authentication required",
        "authentication failed",
        "please log in",
        "please login",
        "login required",
        "unauthorized",
        "invalid_grant",
        "oauth token",
        "401 unauthorized",
        "로그인이 필요",
        "인증이 필요",
    )
    provider_markers = {"codex": ("codex login",)}
    return any(marker in text for marker in (*common_markers, *provider_markers[provider_key]))


def is_blocking_cli_configuration_error(provider_key: str, detail: str) -> bool:
    text = detail.strip()
    return provider_key == "codex" and (
        _is_codex_reasoning_effort_config_error(text)
        or _is_codex_service_tier_config_error(text)
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
        and (
            "service_tier" in lowered
            or ("expected" in lowered and "fast" in lowered and "flex" in lowered)
        )
    )


def _is_codex_reasoning_effort_config_error(detail: str) -> bool:
    lowered = detail.casefold()
    expected_values = ("none", "minimal", "low", "medium", "high", "xhigh")
    return (
        "error loading configuration" in lowered
        and "unknown variant" in lowered
        and (
            "model_reasoning_effort" in lowered
            or (
                "expected" in lowered
                and all(value in lowered for value in expected_values)
            )
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
    match = re.search(
        r"Error loading configuration:\s*(.+?config\.toml)(?::\d+:\d+)?",
        detail,
        re.IGNORECASE,
    )
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
    global_args: tuple[str, ...] = ()
    install_hint: str = ""


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
        global_args=CODEX_NONINTERACTIVE_GLOBAL_ARGS,
        install_hint="Codex CLI를 설치한 뒤 codex login을 실행하세요.",
    ),
}


# Static choices are a recovery path only. Live provider catalogs drive the normal model picker.
FALLBACK_MODEL_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "codex": (("자동 선택 (권장)", ""),),
}


def provider_spec(key: str) -> CliProviderSpec:
    try:
        return PROVIDER_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown AI provider: {key}") from exc


def provider_configs_from_app_config(
    ai_config: dict[str, Any],
) -> dict[str, AiProviderConfig]:
    providers = ai_config.get("providers", {}) if isinstance(ai_config, dict) else {}
    return {
        key: AiProviderConfig.from_dict(key, providers.get(key, {}))
        for key in PROVIDER_SPECS
    }


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
        resolved_path=str(resolved or "") if installed else "",
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
                    *_versioned_local_codex_candidates(local_appdata),
                    str(Path(appdata) / "npm" / "codex.cmd") if appdata else "",
                    shutil.which("codex.cmd") or "",
                    shutil.which("codex.exe") or "",
                ]
            )
            if local_appdata:
                candidates.extend(
                    [
                        str(
                            Path(local_appdata)
                            / "OpenAI"
                            / "Codex"
                            / "bin"
                            / "codex.exe"
                        ),
                        str(
                            Path(local_appdata)
                            / "OpenAI"
                            / "ChatGPT"
                            / "bin"
                            / "codex.exe"
                        ),
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


def _versioned_local_codex_candidates(local_appdata: str) -> list[str]:
    """Find runnable ChatGPT-managed CLIs, newest copy first.

    Desktop updates keep the currently bundled CLI in a versioned directory while
    the stable ``bin/codex.exe`` path can temporarily remain on an older release.
    """

    if not local_appdata:
        return []
    discovered: list[tuple[int, str]] = []
    for product_name in ("ChatGPT", "Codex"):
        bin_dir = Path(local_appdata) / "OpenAI" / product_name / "bin"
        try:
            candidates = list(bin_dir.glob("*/codex.exe"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                if candidate.is_file():
                    discovered.append((candidate.stat().st_mtime_ns, str(candidate)))
            except OSError:
                continue
    discovered.sort(key=lambda item: (item[0], item[1].casefold()), reverse=True)
    return [path for _mtime, path in discovered]


def _is_windowsapps_alias(path: str) -> bool:
    return "windowsapps" in path.casefold()


def build_login_invocation(
    config: AiProviderConfig, working_dir: str = ""
) -> CliInvocation:
    spec = provider_spec(config.key)
    return CliInvocation(
        provider_key=config.key,
        program=resolve_provider_program(config),
        args=[*spec.global_args, *spec.login_args],
        working_dir=working_dir,
        timeout_seconds=config.timeout_seconds,
    )


def build_status_invocation(
    config: AiProviderConfig, working_dir: str = ""
) -> CliInvocation:
    spec = provider_spec(config.key)
    return CliInvocation(
        provider_key=config.key,
        program=resolve_provider_program(config),
        args=[*spec.global_args, *spec.status_args],
        working_dir=working_dir,
        timeout_seconds=min(config.timeout_seconds, 60),
    )


def build_help_invocation(
    config: AiProviderConfig, working_dir: str = ""
) -> CliInvocation:
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
    session_id: str = "",
    codex_sandbox: str = "read-only",
    codex_workspace_root: str = "",
    codex_writable_dirs: tuple[str, ...] = (),
) -> CliInvocation:
    provider_spec(config.key)
    program = resolve_provider_program(config)
    configured_role = (role_prompt or config.role_prompt).strip()
    effective_role = "\n\n".join(
        part for part in (configured_role, NETOPS_ASSISTANT_ROLE_PROMPT) if part
    )
    composed_prompt = compose_agent_prompt(
        prompt, role_prompt=effective_role, context=context
    )
    resume_session_id = session_id.strip()
    sandbox = str(codex_sandbox or "read-only").strip()
    if sandbox not in CODEX_SANDBOX_MODES:
        raise ValueError(f"Unsupported Codex sandbox mode: {sandbox}")
    args = ["-a", "never", "-s", sandbox]
    workspace_root = str(codex_workspace_root or "").strip()
    if workspace_root:
        args.extend(("-C", workspace_root))
    if sandbox == "workspace-write":
        seen_writable_dirs: set[str] = set()
        for raw_path in codex_writable_dirs:
            path = str(raw_path or "").strip()
            key = path.casefold()
            if not path or key in seen_writable_dirs:
                continue
            seen_writable_dirs.add(key)
            args.extend(("--add-dir", path))
    if resume_session_id:
        args.extend(("exec", "resume", "--skip-git-repo-check", "--json"))
    else:
        args.extend(("exec", "--skip-git-repo-check", "--json"))
    if config.model.strip():
        args.extend(["--model", config.model.strip()])
    args.extend(config.extra_args)
    if resume_session_id:
        args.extend((resume_session_id, "-"))
    else:
        args.append("-")

    return CliInvocation(
        provider_key=config.key,
        program=program,
        args=args,
        stdin_text=composed_prompt,
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
    return [
        option
        for option in parse_cli_help_options(help_text)
        if option.flag not in RESERVED_EXTRA_ARG_FLAGS
    ]


def _parse_help_option_line(stripped: str) -> dict[str, str | bool] | None:
    if not stripped.startswith("-"):
        return None
    declaration, description = _split_option_declaration(stripped)
    flags = re.findall(
        r"(?<![\w-])(-[A-Za-z0-9]|--[A-Za-z0-9][A-Za-z0-9-]*)", declaration
    )
    if not flags:
        return None

    long_flags = [flag for flag in flags if flag.startswith("--")]
    flag = long_flags[0] if long_flags else flags[0]
    short_flag = next(
        (item for item in flags if item.startswith("-") and not item.startswith("--")),
        "",
    )
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
    match = re.search(
        rf"{re.escape(flag)}(?:[=\s]+)(<[^>]+>|\[[^\]]+\]|[A-Z][A-Z0-9_-]*(?:\.\.\.)?)",
        declaration,
    )
    if not match:
        return ""
    return match.group(1).strip()


def compose_agent_prompt(
    prompt: str, *, role_prompt: str = "", context: str = ""
) -> str:
    sections = []
    if role_prompt.strip():
        sections.append(f"Agent role:\n{role_prompt.strip()}")
    if context.strip():
        sections.append(f"Shared context:\n{context.strip()}")
    sections.append(f"User request:\n{prompt.strip()}")
    return "\n\n".join(sections).strip()


def parse_provider_event(line: str) -> ProviderEvent | None:
    """Normalize one Codex JSONL record without making it displayable by default."""
    stripped = sanitize_cli_text(line).strip()
    if not stripped:
        return None

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        # Codex is invoked in structured-output mode. A plain line is
        # diagnostic output, never an assistant answer.
        return ProviderEvent("codex", PROVIDER_EVENT_PROGRESS, raw_type="plain")
    if not isinstance(payload, dict):
        return ProviderEvent("codex", PROVIDER_EVENT_PROGRESS, raw_type="json")

    event_type = str(payload.get("type", "") or "").casefold()
    session_id = extract_cli_session_id(stripped)
    if session_id:
        return ProviderEvent(
            "codex",
            PROVIDER_EVENT_SESSION,
            session_id=session_id,
            raw_type=event_type,
        )

    error_text = extract_error_from_cli_line(stripped)
    if not error_text and event_type in {"turn.failed", "turn.error"}:
        error_text = _provider_error_text(payload)
    if error_text:
        return ProviderEvent(
            "codex",
            PROVIDER_EVENT_ERROR,
            text=error_text,
            raw_type=event_type,
            turn_complete=event_type in {"turn.failed", "turn.error"},
        )

    return _parse_codex_provider_event(payload, event_type)


def _parse_codex_provider_event(
    payload: dict[str, Any], event_type: str
) -> ProviderEvent:
    if event_type == "turn.completed":
        return ProviderEvent(
            "codex",
            PROVIDER_EVENT_PROGRESS,
            raw_type=event_type,
            turn_complete=True,
        )

    item = payload.get("item")
    item_dict = item if isinstance(item, dict) else {}
    item_type = str(item_dict.get("type", "") or "").casefold()
    raw_type = f"{event_type}:{item_type}" if item_type else event_type
    item_text = sanitize_cli_text(
        "".join(_collect_text_fragments(item_dict))
    ).strip()

    if event_type == "item.completed" and item_type == "agent_message":
        return ProviderEvent(
            "codex",
            PROVIDER_EVENT_FINAL_TEXT,
            text=item_text,
            raw_type=raw_type,
        )
    if item_type == "reasoning":
        return ProviderEvent(
            "codex",
            PROVIDER_EVENT_REASONING,
            text=item_text,
            raw_type=raw_type,
        )
    if _is_tool_item_type(item_type):
        return ProviderEvent(
            "codex",
            PROVIDER_EVENT_TOOL,
            text=item_text,
            raw_type=raw_type,
        )
    return ProviderEvent("codex", PROVIDER_EVENT_PROGRESS, raw_type=raw_type)


def _is_tool_item_type(item_type: str) -> bool:
    return item_type in {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "tool_call",
        "tool_use",
        "web_search",
    }


def _provider_error_text(payload: dict[str, Any]) -> str:
    for value in (payload.get("error"), payload.get("result"), payload.get("message")):
        if isinstance(value, str) and value:
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return sanitize_cli_text(value).strip()
            if isinstance(decoded, dict):
                nested = decoded.get("message", "")
                if isinstance(nested, str):
                    return sanitize_cli_text(nested).strip()
        elif isinstance(value, dict):
            nested = value.get("message", "")
            if isinstance(nested, str):
                return sanitize_cli_text(nested).strip()
    return ""


def extract_cli_session_id(line: str) -> str:
    """Return the exact session identifier from a Codex initialization event."""
    stripped = sanitize_cli_text(line).strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""

    event_type = str(payload.get("type", "") or "").casefold()
    candidate: object = payload.get("thread_id", "") if event_type == "thread.started" else ""

    session_id = candidate.strip() if isinstance(candidate, str) else ""
    if (
        not session_id
        or len(session_id) > 256
        or any(ord(char) < 32 for char in session_id)
    ):
        return ""
    return session_id


def extract_error_from_cli_line(line: str) -> str:
    """Return only the user-safe message from a structured CLI error event."""
    stripped = sanitize_cli_text(line).strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ""
    if (
        not isinstance(payload, dict)
        or str(payload.get("type", "")).casefold() != "error"
    ):
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
            message = (
                decoded_error.get("message", "")
                if isinstance(decoded_error, dict)
                else ""
            )
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
        target = (
            cache_warning_lines
            if is_codex_model_cache_compatibility_warning(line)
            else regular_lines
        )
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
        score = (
            decoded.count("\ufffd"),
            -sum("\uac00" <= char <= "\ud7a3" for char in decoded),
        )
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
    if (
        "pid" in lowered
        and "process" in lowered
        and ("terminated" in lowered or "success" in lowered)
    ):
        return True
    if (
        "pid" in lowered
        and "프로세스" in stripped
        and ("종료" in stripped or stripped.startswith("성공"))
    ):
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
