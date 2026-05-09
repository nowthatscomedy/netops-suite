from __future__ import annotations

import ipaddress
import posixpath
from pathlib import Path


class ValidationError(ValueError):
    pass


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"{field_name} 값을 입력해 주세요.")
    return text


def validate_ftp_protocol(value: str) -> str:
    protocol = str(value or "").strip().lower()
    if protocol not in {"ftp", "ftps", "sftp"}:
        raise ValidationError("FTP 프로토콜은 ftp, ftps, sftp 중 하나여야 합니다.")
    return protocol


def default_ftp_port(protocol: str, server_mode: bool = False) -> int:
    normalized = validate_ftp_protocol(protocol)
    if server_mode:
        return {"ftp": 2121, "ftps": 2121, "sftp": 2222}[normalized]
    return {"ftp": 21, "ftps": 21, "sftp": 22}[normalized]


def validate_ftp_host(value: str) -> str:
    text = require_text(value, "호스트")
    if any(ch.isspace() for ch in text):
        raise ValidationError("호스트 값에는 공백을 포함할 수 없습니다.")
    return text


def validate_ftp_username(value: str, protocol: str) -> str:
    text = str(value or "").strip()
    if text:
        return text
    if validate_ftp_protocol(protocol) in {"ftp", "ftps"}:
        return "anonymous"
    raise ValidationError("SFTP 사용자명은 비워 둘 수 없습니다.")


def validate_existing_directory(value: str, field_name: str) -> str:
    text = require_text(value, field_name)
    path = Path(text).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValidationError(f"{field_name} 경로가 올바른 폴더가 아닙니다.")
    return str(path)


def validate_optional_existing_directory(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return validate_existing_directory(text, field_name)


def normalize_remote_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "/"
    normalized = posixpath.normpath(text.replace("\\", "/"))
    if normalized in {"", "."}:
        return "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized or "/"


def validate_remote_name(value: str, field_name: str) -> str:
    text = require_text(value, field_name)
    if "/" in text or "\\" in text:
        raise ValidationError(f"{field_name} 값에는 경로 구분자를 넣을 수 없습니다.")
    if text in {".", ".."}:
        raise ValidationError(f"{field_name} 값이 올바르지 않습니다.")
    return text


def validate_ipv4(value: str, field_name: str) -> str:
    text = require_text(value, field_name)
    try:
        ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError as exc:
        raise ValidationError(f"{field_name} 값이 올바른 IPv4 주소가 아닙니다.") from exc
    return text


def validate_optional_ipv4(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return validate_ipv4(text, field_name)


def parse_prefix_value(value: str | int) -> int:
    if isinstance(value, int):
        prefix = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValidationError("Prefix 또는 서브넷 마스크를 입력해 주세요.")
        if text.isdigit():
            prefix = int(text)
        else:
            try:
                prefix = ipaddress.IPv4Network(f"0.0.0.0/{text}").prefixlen
            except (ipaddress.NetmaskValueError, ValueError) as exc:
                raise ValidationError(
                    "Prefix는 1~32 또는 서브넷 마스크(예: 255.255.255.0) 형식이어야 합니다."
                ) from exc
    if not 1 <= int(prefix) <= 32:
        raise ValidationError("Prefix 길이는 1~32 범위여야 합니다.")
    return int(prefix)


def validate_prefix(prefix: str | int) -> int:
    return parse_prefix_value(prefix)


def prefix_to_netmask(prefix: str | int) -> str:
    prefix_length = parse_prefix_value(prefix)
    return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix_length}").netmask)


def format_prefix(prefix: str | int) -> str:
    prefix_length = parse_prefix_value(prefix)
    return f"{prefix_length} / {prefix_to_netmask(prefix_length)}"


def parse_dns_servers(raw_value: str) -> list[str]:
    servers: list[str] = []
    for part in str(raw_value or "").replace("\n", ",").split(","):
        candidate = part.strip()
        if not candidate:
            continue
        validate_ipv4(candidate, "DNS 서버")
        servers.append(candidate)
    return servers


def validate_host_input(value: str) -> str:
    text = require_text(value, "대상")
    if any(ch.isspace() for ch in text):
        raise ValidationError("대상 값에는 공백을 포함할 수 없습니다.")
    return text


def parse_positive_int(
    value: str | int,
    field_name: str,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"{field_name} 값을 입력해 주세요.")
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValidationError(f"{field_name} 값은 숫자여야 합니다.") from exc
    if parsed < minimum:
        raise ValidationError(f"{field_name} 값은 {minimum} 이상이어야 합니다.")
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field_name} 값은 {maximum} 이하여야 합니다.")
    return parsed


def calculate_subnet_details(ip_value: str, prefix_value: str | int) -> dict[str, str]:
    ip_text = validate_ipv4(ip_value, "IPv4")
    prefix_length = parse_prefix_value(prefix_value)
    interface = ipaddress.IPv4Interface(f"{ip_text}/{prefix_length}")
    network = interface.network

    wildcard_mask = ipaddress.IPv4Address(
        int(ipaddress.IPv4Address("255.255.255.255")) ^ int(network.netmask)
    )
    total_addresses = network.num_addresses

    if prefix_length >= 32:
        usable_hosts = 1
        first_host = last_host = str(interface.ip)
        host_range = str(interface.ip)
        notes = "/32 단일 호스트"
    elif prefix_length == 31:
        usable_hosts = 2
        first_host = str(network.network_address)
        last_host = str(network.broadcast_address)
        host_range = f"{first_host} - {last_host}"
        notes = "/31 포인트투포인트 링크"
    else:
        usable_hosts = max(total_addresses - 2, 0)
        first_host = str(network.network_address + 1)
        last_host = str(network.broadcast_address - 1)
        host_range = f"{first_host} - {last_host}" if usable_hosts else "-"
        notes = "-"

    return {
        "ip_address": ip_text,
        "prefix_length": str(prefix_length),
        "cidr": f"{ip_text}/{prefix_length}",
        "network_address": str(network.network_address),
        "broadcast_address": str(network.broadcast_address),
        "netmask": str(network.netmask),
        "wildcard_mask": str(wildcard_mask),
        "first_host": first_host,
        "last_host": last_host,
        "host_range": host_range,
        "usable_hosts": f"{usable_hosts:,}",
        "total_addresses": f"{total_addresses:,}",
        "address_scope": _ipv4_scope_label(interface.ip),
        "notes": notes,
    }


def _ipv4_scope_label(address: ipaddress.IPv4Address) -> str:
    if address.is_loopback:
        return "루프백"
    if address.is_link_local:
        return "링크 로컬"
    if address.is_multicast:
        return "멀티캐스트"
    if address.is_private:
        return "사설"
    if address.is_reserved:
        return "예약"
    return "공인"
