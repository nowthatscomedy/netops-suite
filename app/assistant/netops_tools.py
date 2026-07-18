from __future__ import annotations

import ipaddress
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.models.ftp_models import FtpRemoteEntry
from app.assistant.models import PermissionClass, ToolDescriptor, ToolResult
from app.assistant.registry import ToolRegistry


DEFAULT_EXTERNAL_PING_TARGETS = ("8.8.8.8", "1.1.1.1", "google.com")
DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "PTR", "TXT")
MCP_TOOL_ALIASES = {
    "ping": ("net.ping", "netops.ping"),
    "ping_batch": ("net.ping.batch", "netops.ping.batch"),
    "external_ping": ("net.external_ping", "netops.external_ping", "net.internet.ping"),
    "tcp_check": ("net.tcp_check", "netops.tcp_check"),
    "tcp_batch": ("net.tcp.batch", "netops.tcp.batch"),
    "subnet_calculate": ("net.subnet.calculate", "netops.subnet.calculate"),
    "dns_lookup": ("net.dns.lookup", "netops.dns.lookup"),
    "dns_flush_cache": ("net.dns.flush_cache", "netops.dns.flush_cache"),
    "public_ip": ("net.public_ip", "netops.public_ip"),
    "ipconfig": ("net.ipconfig.read", "netops.ipconfig"),
    "route_print": ("net.route.print", "netops.route.print"),
    "arp_table": ("net.arp.table", "netops.arp.table"),
    "interface_snapshot": ("net.interface.snapshot", "netops.adapters.list"),
    "app_paths": ("app.paths", "netops.app.paths"),
    "artifacts_list": ("artifacts.list", "netops.artifacts.list"),
    "set_dns": ("net.interface.set_dns", "netops.adapter.set_dns"),
    "set_dhcp": ("net.interface.set_dhcp", "netops.adapter.set_dhcp"),
    "set_static_ip": ("net.interface.set_static_ip", "netops.adapter.set_static_ip"),
    "wifi_status": ("wifi.status", "net.wifi.status", "netops.wifi.status"),
    "wifi_scan_nearby": (
        "wifi.scan_nearby",
        "wifi.scan",
        "wireless_scan",
        "wireless.scan",
        "net.wifi.scan_nearby",
        "net.wifi.scan",
        "netops.wifi.scan_nearby",
        "netops.wifi.scan",
        "wifi.nearby.scan",
    ),
    "oui_lookup": ("oui.lookup", "net.oui.lookup", "netops.oui.lookup"),
    "oui_cache_summary": (
        "oui.cache_summary",
        "net.oui.cache_summary",
        "netops.oui.cache_summary",
    ),
    "oui_cache_refresh": (
        "oui.cache.refresh",
        "net.oui.cache.refresh",
        "netops.oui.cache.refresh",
    ),
    "inspector_profiles_list": (
        "inspector.profiles.list",
        "netops.inspector.profiles.list",
    ),
    "config_builder_profiles_list": (
        "config_builder.profiles.list",
        "netops.config_builder.profiles.list",
    ),
    "ip_profiles": ("profiles.ip.list", "netops.profiles.ip.list"),
    "ftp_profiles": ("profiles.ftp.list", "netops.profiles.ftp.list"),
    "scp_profiles": ("profiles.scp.list", "netops.profiles.scp.list"),
    "arp_scan_candidates": ("net.arp.scan_candidates", "netops.arp.scan_candidates"),
    "arp_scan": ("net.arp.scan", "netops.arp.scan"),
    "iperf_status": ("net.iperf.status", "netops.iperf.status"),
    "iperf_client_test": ("net.iperf.client_test", "netops.iperf.client_test"),
    "public_iperf_cached": ("net.iperf.public.cached", "netops.iperf.public.cached"),
    "public_iperf_refresh": ("net.iperf.public.refresh", "netops.iperf.public.refresh"),
    "ftp_client_runtime": (
        "file_transfer.ftp.client.runtime",
        "netops.ftp.client.runtime",
    ),
    "ftp_server_runtime": (
        "file_transfer.ftp.server.runtime",
        "netops.ftp.server.runtime",
    ),
    "ftp_connect": ("file_transfer.ftp.connect", "netops.ftp.connect"),
    "ftp_disconnect": ("file_transfer.ftp.disconnect", "netops.ftp.disconnect"),
    "ftp_list": ("file_transfer.ftp.list", "netops.ftp.list"),
    "ftp_upload": ("file_transfer.ftp.upload", "netops.ftp.upload"),
    "ftp_download": ("file_transfer.ftp.download", "netops.ftp.download"),
    "ftp_mkdir": ("file_transfer.ftp.mkdir", "netops.ftp.mkdir"),
    "ftp_rename": ("file_transfer.ftp.rename", "netops.ftp.rename"),
    "ftp_delete": ("file_transfer.ftp.delete", "netops.ftp.delete"),
    "scp_client_runtime": (
        "file_transfer.scp.client.runtime",
        "netops.scp.client.runtime",
    ),
    "scp_upload": ("file_transfer.scp.upload", "netops.scp.upload"),
    "scp_download": ("file_transfer.scp.download", "netops.scp.download"),
    "tftp_runtime": ("file_transfer.tftp.runtime", "netops.tftp.runtime"),
    "tftp_upload": ("file_transfer.tftp.upload", "netops.tftp.upload"),
    "tftp_download": ("file_transfer.tftp.download", "netops.tftp.download"),
    "update_check": ("app.update.check", "netops.update.check"),
}
TOOL_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "message": {"type": "string"},
        "details": {"type": "string"},
        "data": {},
    },
    "required": ["success", "message"],
}


class NetOpsToolValidationError(ValueError):
    pass


def build_netops_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for descriptor, handler in NETOPS_TOOL_SPECS:
        registry.register(descriptor, handler)
    return registry


def list_netops_tool_descriptors() -> list[ToolDescriptor]:
    return list(NETOPS_TOOL_DESCRIPTORS)


def run_netops_tool(
    state: Any, tool_name: str, arguments: Mapping[str, Any] | None = None
) -> ToolResult:
    return build_netops_tool_registry().execute(state, tool_name, dict(arguments or {}))


def _object_schema(
    properties: dict[str, Any], required: Sequence[str] = ()
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
    }


def _descriptor(
    *,
    name: str,
    display_name: str,
    description: str,
    permission: PermissionClass,
    input_schema: dict[str, Any],
    risk_level: str = "low",
    admin_required: bool = False,
    approval_required: bool | None = None,
    impact: str = "",
    reversibility: str = "Read-only diagnostic.",
    timeout_seconds: int = 30,
    tags: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> ToolDescriptor:
    descriptor_aliases = tuple(
        dict.fromkeys((*MCP_TOOL_ALIASES.get(name, ()), *aliases))
    )
    return ToolDescriptor(
        name=name,
        display_name=display_name,
        description=description,
        permission_class=permission,
        category="netops",
        input_schema=input_schema,
        output_schema=TOOL_RESULT_SCHEMA,
        admin_required=admin_required,
        approval_required=approval_required,
        risk_level=risk_level,
        impact=impact,
        reversibility=reversibility,
        timeout_seconds=timeout_seconds,
        aliases=descriptor_aliases,
        tags=("netops", *tags),
        metadata={
            "risk_level": risk_level,
            "impact": impact,
            "reversibility": reversibility,
            "timeout_seconds": timeout_seconds,
            "tags": ("netops", *tags),
        },
    )


def _required_service(state: Any, attr_name: str) -> Any:
    service = getattr(state, attr_name, None)
    if service is None:
        raise RuntimeError(f"AppState service is not available: {attr_name}")
    return service


def _text_arg(
    args: Mapping[str, Any], name: str, *, field_label: str | None = None
) -> str:
    value = str(args.get(name, "") or "").strip()
    if not value:
        raise NetOpsToolValidationError(f"{field_label or name} is required.")
    if "\r" in value or "\n" in value:
        raise NetOpsToolValidationError(
            f"{field_label or name} cannot contain line breaks."
        )
    return value


def _optional_text_arg(
    args: Mapping[str, Any], name: str, *, field_label: str | None = None
) -> str:
    value = str(args.get(name, "") or "").strip()
    if "\r" in value or "\n" in value:
        raise NetOpsToolValidationError(
            f"{field_label or name} cannot contain line breaks."
        )
    return value


def _bool_arg(args: Mapping[str, Any], name: str, *, default: bool = False) -> bool:
    raw_value = args.get(name, default)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    text = str(raw_value or "").strip().casefold()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    raise NetOpsToolValidationError(f"{name} must be a boolean.")


def _text_list_arg(
    args: Mapping[str, Any], name: str, *, required: bool = False, maximum: int = 64
) -> list[str]:
    raw_value = args.get(name, [])
    if isinstance(raw_value, str):
        values = [
            item.strip()
            for item in raw_value.replace("\n", ",").split(",")
            if item.strip()
        ]
    elif isinstance(raw_value, Sequence) and not isinstance(
        raw_value, (bytes, bytearray)
    ):
        values = [
            str(item or "").strip() for item in raw_value if str(item or "").strip()
        ]
    else:
        raise NetOpsToolValidationError(f"{name} must be a list of strings.")
    if required and not values:
        raise NetOpsToolValidationError(f"{name} must contain at least one value.")
    if len(values) > maximum:
        raise NetOpsToolValidationError(f"{name} supports at most {maximum} values.")
    for value in values:
        if "\r" in value or "\n" in value:
            raise NetOpsToolValidationError(
                f"{name} values cannot contain line breaks."
            )
    return values


def _protocol_arg(args: Mapping[str, Any], *, default: str = "ftp") -> str:
    protocol = str(args.get("protocol", default) or default).strip().casefold()
    if protocol not in {"ftp", "ftps", "sftp"}:
        raise NetOpsToolValidationError("protocol must be one of: ftp, ftps, sftp.")
    return protocol


def _remote_path_arg(
    args: Mapping[str, Any], name: str = "remote_path", *, required: bool = True
) -> str:
    value = _optional_text_arg(args, name, field_label=name)
    if not value and required:
        raise NetOpsToolValidationError(f"{name} is required.")
    return value


def _ftp_remote_entries_arg(args: Mapping[str, Any]) -> list[FtpRemoteEntry]:
    raw_entries = args.get("entries")
    if raw_entries is None:
        remote_paths = _text_list_arg(args, "remote_paths", required=True, maximum=64)
        return [
            FtpRemoteEntry(
                name=PurePosixPath(path).name or path,
                entry_type="dir" if str(path).endswith("/") else "file",
                remote_path=path,
            )
            for path in remote_paths
        ]
    if not isinstance(raw_entries, Sequence) or isinstance(
        raw_entries, (str, bytes, bytearray)
    ):
        raise NetOpsToolValidationError("entries must be a list.")
    entries: list[FtpRemoteEntry] = []
    for item in raw_entries:
        if isinstance(item, FtpRemoteEntry):
            entries.append(item)
            continue
        if not isinstance(item, Mapping):
            raise NetOpsToolValidationError("entries items must be objects.")
        remote_path = str(item.get("remote_path", item.get("path", "")) or "").strip()
        if not remote_path:
            raise NetOpsToolValidationError("entries items require remote_path.")
        entry_type = (
            str(item.get("entry_type", item.get("type", "file")) or "file")
            .strip()
            .casefold()
        )
        entries.append(
            FtpRemoteEntry(
                name=str(
                    item.get("name", PurePosixPath(remote_path).name or remote_path)
                    or ""
                ),
                entry_type="dir"
                if entry_type == "dir" or remote_path.endswith("/")
                else "file",
                size_bytes=int(item.get("size_bytes", 0) or 0),
                modified_at=str(item.get("modified_at", "") or ""),
                permissions=str(item.get("permissions", "") or ""),
                remote_path=remote_path,
            )
        )
    if not entries:
        raise NetOpsToolValidationError("entries must contain at least one item.")
    return entries


def _target_arg(args: Mapping[str, Any], name: str = "target") -> str:
    target = _text_arg(args, name, field_label=name)
    if len(target) > 253:
        raise NetOpsToolValidationError(f"{name} is too long.")
    if any(ch.isspace() for ch in target):
        raise NetOpsToolValidationError(f"{name} cannot contain whitespace.")
    if target.startswith("-"):
        raise NetOpsToolValidationError(f"{name} cannot start with an option prefix.")
    return target


def _interface_arg(args: Mapping[str, Any]) -> str:
    interface_name = _text_arg(args, "interface_name", field_label="interface_name")
    if len(interface_name) > 120:
        raise NetOpsToolValidationError("interface_name is too long.")
    return interface_name


def _int_arg(
    args: Mapping[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise NetOpsToolValidationError(f"{name} must be an integer.") from exc
    if value < minimum or value > maximum:
        raise NetOpsToolValidationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _port_arg(args: Mapping[str, Any]) -> int:
    return _int_arg(args, "port", default=0, minimum=1, maximum=65535)


def _ipv4_arg(args: Mapping[str, Any], name: str, *, required: bool = True) -> str:
    value = str(args.get(name, "") or "").strip()
    if not value:
        if required:
            raise NetOpsToolValidationError(f"{name} is required.")
        return ""
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as exc:
        raise NetOpsToolValidationError(
            f"{name} must be a valid IPv4 address."
        ) from exc


def _prefix_arg(args: Mapping[str, Any]) -> int:
    return _int_arg(args, "prefix", default=0, minimum=1, maximum=32)


def _dns_servers_arg(args: Mapping[str, Any], *, required: bool = False) -> list[str]:
    raw_value = args.get("dns_servers", [])
    if isinstance(raw_value, str):
        values = [
            item.strip()
            for item in raw_value.replace("\n", ",").split(",")
            if item.strip()
        ]
    elif isinstance(raw_value, Sequence) and not isinstance(
        raw_value, (bytes, bytearray)
    ):
        values = [
            str(item or "").strip() for item in raw_value if str(item or "").strip()
        ]
    else:
        raise NetOpsToolValidationError("dns_servers must be a list of IPv4 addresses.")
    if required and not values:
        raise NetOpsToolValidationError("dns_servers must contain at least one server.")
    if len(values) > 4:
        raise NetOpsToolValidationError("dns_servers supports at most four servers.")
    normalized: list[str] = []
    for value in values:
        try:
            normalized.append(str(ipaddress.IPv4Address(value)))
        except ipaddress.AddressValueError as exc:
            raise NetOpsToolValidationError(
                f"Invalid DNS server IPv4 address: {value}"
            ) from exc
    return normalized


def _record_type_arg(args: Mapping[str, Any]) -> str:
    record_type = str(args.get("record_type", "A") or "A").strip().upper()
    if record_type not in DNS_RECORD_TYPES:
        raise NetOpsToolValidationError(
            f"record_type must be one of: {', '.join(DNS_RECORD_TYPES)}"
        )
    return record_type


def _target_list_arg(args: Mapping[str, Any]) -> list[str]:
    raw_targets = args.get("targets") or list(DEFAULT_EXTERNAL_PING_TARGETS)
    if isinstance(raw_targets, str):
        targets = [
            item.strip()
            for item in raw_targets.replace("\n", ",").split(",")
            if item.strip()
        ]
    elif isinstance(raw_targets, Sequence) and not isinstance(
        raw_targets, (bytes, bytearray)
    ):
        targets = [
            str(item or "").strip() for item in raw_targets if str(item or "").strip()
        ]
    else:
        raise NetOpsToolValidationError(
            "targets must be a list of hostnames or IP addresses."
        )
    if not targets:
        raise NetOpsToolValidationError("targets must contain at least one target.")
    if len(targets) > 6:
        raise NetOpsToolValidationError("external_ping supports at most six targets.")
    return [_validate_target_value(target, "targets") for target in targets]


def _required_target_list_arg(
    args: Mapping[str, Any], *, maximum: int = 16
) -> list[str]:
    targets = _text_list_arg(args, "targets", required=True, maximum=maximum)
    return [_validate_target_value(target, "targets") for target in targets]


def _ports_arg(args: Mapping[str, Any], *, maximum: int = 16) -> list[int]:
    raw_ports = args.get("ports")
    if raw_ports is None and "port" in args:
        raw_ports = [args.get("port")]
    values = _text_list_arg(
        {"ports": raw_ports or []}, "ports", required=True, maximum=maximum
    )
    ports: list[int] = []
    for value in values:
        try:
            port = int(value)
        except (TypeError, ValueError) as exc:
            raise NetOpsToolValidationError(
                "ports must contain TCP port numbers."
            ) from exc
        if port < 1 or port > 65535:
            raise NetOpsToolValidationError("ports must be between 1 and 65535.")
        ports.append(port)
    return ports


def _validate_target_value(target: str, field_label: str) -> str:
    if len(target) > 253:
        raise NetOpsToolValidationError(
            f"{field_label} contains a target that is too long."
        )
    if any(ch.isspace() for ch in target):
        raise NetOpsToolValidationError(
            f"{field_label} targets cannot contain whitespace."
        )
    if target.startswith("-"):
        raise NetOpsToolValidationError(
            f"{field_label} targets cannot start with an option prefix."
        )
    return target


def _plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_data(item) for item in value]
    return value


def _plain_result_row(value: Any) -> dict[str, Any]:
    plain = _plain_data(value)
    if isinstance(plain, Mapping):
        return {str(key): item for key, item in plain.items()}
    if hasattr(value, "__dict__"):
        return {
            str(key): _plain_data(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return {"value": plain}


def _path_metadata(name: str, value: Any) -> dict[str, Any]:
    path_text = str(value or "")
    data: dict[str, Any] = {
        "name": name,
        "path": path_text,
        "exists": False,
        "is_dir": False,
        "is_file": False,
        "size_bytes": None,
        "modified_at": "",
    }
    if not path_text:
        return data
    try:
        path = Path(path_text)
        data["exists"] = path.exists()
        data["is_dir"] = path.is_dir()
        data["is_file"] = path.is_file()
        if data["exists"]:
            stat = path.stat()
            data["size_bytes"] = int(stat.st_size) if data["is_file"] else None
            data["modified_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
            )
    except OSError as exc:
        data["error"] = str(exc)
    return data


def _iter_public_attrs(value: Any) -> list[tuple[str, Any]]:
    if value is None:
        return []
    field_map = getattr(value, "__dataclass_fields__", None)
    if isinstance(field_map, Mapping):
        return [
            (name, getattr(value, name, None))
            for name in field_map
            if not str(name).startswith("_")
        ]
    try:
        items = vars(value).items()
    except TypeError:
        return []
    return [
        (str(name), item)
        for name, item in items
        if not str(name).startswith("_") and not callable(item)
    ]


def _app_paths_payload(paths: Any) -> list[dict[str, Any]]:
    return [_path_metadata(name, value) for name, value in _iter_public_attrs(paths)]


def _artifact_roots(paths: Any) -> list[tuple[str, Path]]:
    data_root = getattr(paths, "data_root", None)
    roots: list[tuple[str, Any]] = [
        ("logs", getattr(paths, "logs_dir", None)),
        ("exports", getattr(paths, "exports_dir", None)),
    ]
    if data_root:
        roots.extend(
            [
                ("inspector_runs", Path(data_root) / "inspector" / "runs"),
                ("config_builder", Path(data_root) / "config_builder"),
            ]
        )
    normalized: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for label, root in roots:
        if not root:
            continue
        path = Path(root)
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append((label, path))
    return normalized


def _artifact_kind(path: Path) -> str:
    text = str(path).casefold()
    suffix = path.suffix.casefold()
    if "backup" in text:
        return "backup"
    if "session_logs" in text or suffix == ".log":
        return "log"
    if suffix in {".xlsx", ".xls"}:
        return "excel"
    if suffix == ".txt":
        return "text"
    if suffix in {".csv", ".tsv"}:
        return "table"
    return "file"


def _is_hidden_artifact(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _artifact_item(path: Path, root_label: str, root: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        relative_path = str(path.relative_to(root))
    except ValueError:
        relative_path = path.name
    return {
        "kind": _artifact_kind(path),
        "name": path.name,
        "path": str(path),
        "root": root_label,
        "relative_path": relative_path,
        "size_bytes": int(stat.st_size),
        "modified_at": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
        ),
        "modified_timestamp": float(stat.st_mtime),
    }


def _list_artifacts(
    paths: Any, *, limit: int, include_hidden: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    roots = [
        {"label": label, **_path_metadata(label, root)}
        for label, root in _artifact_roots(paths)
    ]
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for root_info in roots:
        root = Path(root_info["path"])
        if not root_info.get("exists") or not root_info.get("is_dir"):
            continue
        try:
            candidates = root.rglob("*")
            for path in candidates:
                if not path.is_file() or path.name == ".gitkeep":
                    continue
                path_key = str(path.resolve()).casefold()
                if path_key in seen_paths:
                    continue
                seen_paths.add(path_key)
                if not include_hidden and _is_hidden_artifact(path.relative_to(root)):
                    continue
                item = _artifact_item(path, str(root_info["label"]), root)
                if item is not None:
                    artifacts.append(item)
        except OSError as exc:
            root_info["error"] = str(exc)
    artifacts.sort(key=lambda item: item["modified_timestamp"], reverse=True)
    total_count = len(artifacts)
    return artifacts[:limit], roots, total_count


def _path_from_state(state: Any, *parts: str) -> Path | None:
    paths = getattr(state, "paths", None)
    data_root = getattr(paths, "data_root", None)
    if data_root is None:
        return None
    return Path(data_root).joinpath(*parts)


def _read_only_inspector_service(state: Any) -> Any:
    service = getattr(state, "inspector_service", None)
    if service is not None:
        return service

    from netops_suite.modules.inspector import InspectorService

    service = object.__new__(InspectorService)
    package_root = Path(__file__).resolve().parents[2] / "netops_suite" / "modules"
    data_dir = _path_from_state(state, "inspector") or Path.cwd() / "inspector"
    service.runtime_dir = package_root / "inspector_runtime"
    service.work_dir = data_dir / "runs"
    service.user_data_dir = data_dir
    service.vendor_profiles_dir = package_root / "inspector" / "vendor_profiles"
    return service


def _read_only_config_builder_service(state: Any) -> Any:
    service = getattr(state, "config_builder_service", None)
    if service is not None:
        return service

    from netops_suite.modules.config_builder import ConfigBuilderService

    user_profiles_dir = _path_from_state(state, "config_builder", "profiles")
    profiles_dir = (
        user_profiles_dir
        if user_profiles_dir is not None and user_profiles_dir.exists()
        else None
    )
    return ConfigBuilderService(profiles_dir=profiles_dir)


def _tool_result(
    *,
    success: bool,
    message: str,
    details: str = "",
    data: Any = None,
    status: str | None = None,
) -> ToolResult:
    normalized_status = status or ("ok" if success else "error")
    metadata = {"message": message, "details": details, "status": normalized_status}
    output = "\n".join(part for part in (message.strip(), details.strip()) if part)
    if success:
        return ToolResult.ok(output, payload=data, **metadata)
    return ToolResult.failed(
        message or "Tool execution failed.", output=details, payload=data, **metadata
    )


def _operation_tool_result(result: Any) -> ToolResult:
    return ToolResult.from_operation_result(result)


def _format_operation_result(result: Any) -> str:
    status = "success" if bool(getattr(result, "success", False)) else "failure"
    message = str(getattr(result, "message", "") or "").strip()
    details = str(getattr(result, "details", "") or "").strip()
    parts = [status]
    if message:
        parts.append(message)
    if details:
        parts.append(details)
    return "\n".join(parts)


def _handle_ping(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ping_service")
    target = _target_arg(args)
    count = _int_arg(args, "count", default=4, minimum=1, maximum=10)
    timeout_ms = _int_arg(args, "timeout_ms", default=4000, minimum=500, maximum=15000)
    continuous, cancel_event = _continuous_probe_options(args)
    if continuous:
        kwargs: dict[str, Any] = {
            "count": count,
            "timeout_ms": timeout_ms,
            "max_workers": 1,
            "continuous": True,
        }
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        results = service.run_multi_ping(target, **kwargs)
        return _ping_batch_tool_result([target], results)
    return _operation_tool_result(
        service.quick_ping(target, count=count, timeout_ms=timeout_ms)
    )


def _handle_ping_batch(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ping_service")
    targets = _required_target_list_arg(args, maximum=16)
    count = _int_arg(args, "count", default=2, minimum=1, maximum=10)
    timeout_ms = _int_arg(args, "timeout_ms", default=4000, minimum=500, maximum=10000)
    continuous, cancel_event = _continuous_probe_options(args)
    max_workers = _int_arg(
        args, "max_workers", default=min(8, len(targets)), minimum=1, maximum=16
    )
    kwargs: dict[str, Any] = {
        "count": count,
        "timeout_ms": timeout_ms,
        "max_workers": max_workers,
        "continuous": continuous,
    }
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    results = service.run_multi_ping("\n".join(targets), **kwargs)
    return _ping_batch_tool_result(targets, results)


def _handle_external_ping(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ping_service")
    targets = _target_list_arg(args)
    count = _int_arg(args, "count", default=2, minimum=1, maximum=10)
    timeout_ms = _int_arg(args, "timeout_ms", default=4000, minimum=500, maximum=15000)
    continuous, cancel_event = _continuous_probe_options(args)

    if continuous:
        max_workers = min(8, len(targets))
        kwargs: dict[str, Any] = {
            "count": count,
            "timeout_ms": timeout_ms,
            "max_workers": max_workers,
            "continuous": True,
        }
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        results = service.run_multi_ping("\n".join(targets), **kwargs)
        return _ping_batch_tool_result(targets, results)

    rows: list[dict[str, Any]] = []
    details: list[str] = []
    for target in targets:
        if cancel_event is not None and cancel_event.is_set():
            rows.append(
                {
                    "target": target,
                    "success": False,
                    "message": "",
                    "details": "Execution stopped before this target was attempted.",
                    "status": "omitted",
                }
            )
            details.append(
                f"[{target}]\nStatus: omitted\nExecution stopped before this target was attempted."
            )
            continue
        try:
            result = service.quick_ping(target, count=count, timeout_ms=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - preserve the remaining targets
            rows.append(
                {
                    "target": target,
                    "success": False,
                    "message": "",
                    "details": str(exc),
                    "status": "error",
                }
            )
            details.append(f"[{target}]\nfailure\n{exc}")
            continue
        rows.append(
            {
                "target": target,
                "success": bool(getattr(result, "success", False)),
                "message": str(getattr(result, "message", "") or ""),
                "details": str(getattr(result, "details", "") or ""),
            }
        )
        details.append(f"[{target}]\n{_format_operation_result(result)}")

    reachable = sum(1 for row in rows if row["success"])
    return _tool_result(
        success=reachable > 0,
        message=f"External ping completed: {reachable}/{len(rows)} reachable.",
        details="\n\n".join(details),
        data=rows,
        status="ok" if reachable == len(rows) else "partial" if reachable else "error",
    )


def _handle_tcp_check(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "tcp_check_service")
    target = _target_arg(args)
    port = _port_arg(args)
    count = _int_arg(args, "count", default=2, minimum=1, maximum=10)
    timeout_ms = _int_arg(args, "timeout_ms", default=4000, minimum=500, maximum=15000)
    continuous, cancel_event = _continuous_probe_options(args)
    kwargs: dict[str, Any] = {
        "count": count,
        "timeout_ms": timeout_ms,
        "max_workers": 1,
        "continuous": continuous,
    }
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    results = service.run_multi_check(target, str(port), **kwargs)
    return _tcp_batch_tool_result([(target, port)], results, label="TCP check")


def _handle_tcp_batch(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "tcp_check_service")
    targets = _required_target_list_arg(args, maximum=16)
    ports = _ports_arg(args, maximum=16)
    endpoint_count = len(targets) * len(ports)
    if endpoint_count > 64:
        raise NetOpsToolValidationError(
            "tcp_batch supports at most 64 target/port endpoints per run."
        )
    count = _int_arg(args, "count", default=2, minimum=1, maximum=10)
    timeout_ms = _int_arg(args, "timeout_ms", default=4000, minimum=500, maximum=10000)
    continuous, cancel_event = _continuous_probe_options(args)
    max_workers = _int_arg(
        args, "max_workers", default=min(16, endpoint_count), minimum=1, maximum=32
    )
    kwargs: dict[str, Any] = {
        "count": count,
        "timeout_ms": timeout_ms,
        "max_workers": max_workers,
        "continuous": continuous,
    }
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    results = service.run_multi_check(
        "\n".join(targets),
        ",".join(str(port) for port in ports),
        **kwargs,
    )
    expected_endpoints = [(target, port) for target in targets for port in ports]
    return _tcp_batch_tool_result(expected_endpoints, results)


def _ping_batch_tool_result(targets: list[str], results: list[Any]) -> ToolResult:
    by_target: dict[str, Any] = {}
    for result in results:
        result_target = str(getattr(result, "target", "") or "").strip()
        if result_target:
            by_target.setdefault(result_target.casefold(), result)

    rows: list[dict[str, Any]] = []
    details: list[str] = []
    reachable = 0
    completed = 0
    for target in targets:
        result = by_target.get(target.casefold())
        if result is None:
            rows.append(
                {
                    "target": target,
                    "success": False,
                    "status": "omitted",
                    "error": "No result was returned for this requested target.",
                }
            )
            details.append(
                f"Target: {target}\nStatus: omitted\nError: No result was returned for this requested target."
            )
            continue
        completed += 1
        is_reachable = (
            bool(getattr(result, "success", False))
            or int(getattr(result, "received", 0) or 0) > 0
        )
        reachable += int(is_reachable)
        rows.append(_plain_result_row(result))
        details.append(_format_ping_result(result))

    requested = len(targets)
    status = (
        "ok"
        if requested > 0 and completed == requested and reachable == requested
        else "partial"
        if completed > 0 or reachable > 0
        else "error"
    )
    return _tool_result(
        success=reachable > 0,
        message=(
            f"Ping batch completed: {reachable}/{requested} target(s) reachable; "
            f"{completed}/{requested} result(s) returned."
        ),
        details="\n\n".join(details),
        data=rows,
        status=status,
    )


def _tcp_batch_tool_result(
    expected_endpoints: list[tuple[str, int]],
    results: list[Any],
    *,
    label: str = "TCP batch",
) -> ToolResult:
    by_endpoint: dict[tuple[str, int], Any] = {}
    for result in results:
        target = str(getattr(result, "target", "") or "").strip().casefold()
        port = int(getattr(result, "port", 0) or 0)
        if target and port:
            by_endpoint.setdefault((target, port), result)

    rows: list[dict[str, Any]] = []
    details: list[str] = []
    reachable = 0
    completed = 0
    for target, port in expected_endpoints:
        result = by_endpoint.get((target.casefold(), int(port)))
        if result is None:
            rows.append(
                {
                    "target": target,
                    "port": port,
                    "success": False,
                    "status": "omitted",
                    "error": "No result was returned for this requested endpoint.",
                }
            )
            details.append(
                f"Target: {target}:{port}\nStatus: omitted\nError: No result was returned for this requested endpoint."
            )
            continue
        completed += 1
        is_reachable = int(getattr(result, "successful", 0) or 0) > 0
        reachable += int(is_reachable)
        rows.append(_plain_result_row(result))
        details.append(_format_tcp_check_result(result))

    requested = len(expected_endpoints)
    status = (
        "ok"
        if requested > 0 and completed == requested and reachable == requested
        else "partial"
        if completed > 0 or reachable > 0
        else "error"
    )
    return _tool_result(
        success=reachable > 0,
        message=(
            f"{label} completed: {reachable}/{requested} endpoint(s) reachable; "
            f"{completed}/{requested} result(s) returned."
        ),
        details="\n\n".join(details),
        data=rows,
        status=status,
    )


def _continuous_probe_options(args: Mapping[str, Any]) -> tuple[bool, Any]:
    continuous = _bool_arg(args, "continuous", default=False)
    cancel_event = args.get("_cancel_event")
    if continuous and cancel_event is None:
        raise NetOpsToolValidationError(
            "continuous probe execution requires a cooperative cancellation context."
        )
    return continuous, cancel_event


def _handle_subnet_calculate(_state: Any, args: dict[str, Any]) -> ToolResult:
    cidr = _optional_text_arg(args, "cidr")
    if cidr:
        network_text = cidr
    else:
        ip_address = _target_arg(args, "ip_address")
        raw_prefix = args.get("prefix")
        if raw_prefix is None:
            raise NetOpsToolValidationError(
                "prefix is required when cidr is not provided."
            )
        try:
            address = ipaddress.ip_address(ip_address)
        except ValueError as exc:
            raise NetOpsToolValidationError(
                "ip_address must be a valid IP address."
            ) from exc
        max_prefix = 32 if address.version == 4 else 128
        prefix = _int_arg(args, "prefix", default=0, minimum=0, maximum=max_prefix)
        network_text = f"{address}/{prefix}"

    try:
        network = ipaddress.ip_network(network_text, strict=False)
    except ValueError as exc:
        raise NetOpsToolValidationError(
            "cidr must be a valid IP network such as 192.168.1.0/24."
        ) from exc

    include_hosts = _bool_arg(args, "include_hosts", default=False)
    max_hosts = _int_arg(args, "max_hosts", default=64, minimum=1, maximum=256)
    host_samples: list[str] = []
    first_host = ""
    last_host = ""
    for index, host in enumerate(network.hosts()):
        host_text = str(host)
        if index == 0:
            first_host = host_text
        last_host = host_text
        if include_hosts and len(host_samples) < max_hosts:
            host_samples.append(host_text)
        if (
            (not include_hosts or len(host_samples) >= max_hosts)
            and network.num_addresses > 65536
            and first_host
        ):
            break

    if first_host and network.num_addresses > 65536:
        if network.version == 4 and network.prefixlen <= 30:
            last_host = str(ipaddress.ip_address(int(network.broadcast_address) - 1))
        else:
            last_host = str(network.broadcast_address)

    if network.version == 4 and network.prefixlen <= 30:
        host_count = max(0, int(network.num_addresses) - 2)
    else:
        host_count = int(network.num_addresses)
    data = {
        "input": network_text,
        "network": str(network),
        "version": network.version,
        "network_address": str(network.network_address),
        "broadcast_address": str(network.broadcast_address)
        if network.version == 4
        else "",
        "netmask": str(network.netmask),
        "hostmask": str(network.hostmask),
        "prefixlen": network.prefixlen,
        "num_addresses": int(network.num_addresses),
        "usable_host_count": int(host_count),
        "first_host": first_host,
        "last_host": last_host,
        "is_private": bool(network.is_private),
        "is_global": bool(network.is_global),
        "hosts": host_samples,
        "hosts_truncated": include_hosts and host_count > len(host_samples),
    }
    detail_lines = [
        f"Network: {data['network']}",
        f"Address family: IPv{data['version']}",
        f"Network address: {data['network_address']}",
        f"Netmask: {data['netmask']}",
        f"Host range: {data['first_host'] or '-'} - {data['last_host'] or '-'}",
        f"Usable hosts: {data['usable_host_count']}",
    ]
    if data["broadcast_address"]:
        detail_lines.append(f"Broadcast: {data['broadcast_address']}")
    if include_hosts:
        detail_lines.append(
            "Host samples: " + (", ".join(host_samples) if host_samples else "-")
        )
    return _tool_result(
        success=True,
        message=f"Subnet calculated: {network}",
        details="\n".join(detail_lines),
        data=data,
    )


def _handle_dns_lookup(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "dns_service")
    query = _target_arg(args, "query")
    record_type = _record_type_arg(args)
    server = _optional_text_arg(args, "server")
    if server:
        _validate_target_value(server, "server")
    return _operation_tool_result(service.lookup(query, record_type, server))


def _handle_dns_flush_cache(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "dns_service")
    return _operation_tool_result(service.flush_dns_cache())


def _handle_public_ip(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "public_ip_service")
    timeout_seconds = _int_arg(
        args, "timeout_seconds", default=5, minimum=1, maximum=30
    )
    return _operation_tool_result(
        service.check_public_ip(timeout_seconds=timeout_seconds)
    )


def _handle_tracert(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "trace_service")
    target = _target_arg(args)
    resolve_names = _bool_arg(args, "resolve_names", default=True)
    return _operation_tool_result(
        service.run_tracert(target, resolve_names=resolve_names)
    )


def _handle_pathping(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "trace_service")
    target = _target_arg(args)
    resolve_names = _bool_arg(args, "resolve_names", default=True)
    return _operation_tool_result(
        service.run_pathping(target, resolve_names=resolve_names)
    )


def _handle_ipconfig(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "trace_service")
    return _operation_tool_result(service.run_ipconfig_all())


def _handle_route_print(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "trace_service")
    return _operation_tool_result(service.run_route_print())


def _handle_arp_table(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "trace_service")
    return _operation_tool_result(service.run_arp_table())


def _handle_interface_snapshot(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "network_interface_service")
    interface_name = _optional_text_arg(args, "interface_name")
    adapters = list(service.list_adapters())
    if interface_name:
        normalized = interface_name.casefold()
        adapters = [
            adapter
            for adapter in adapters
            if str(getattr(adapter, "name", "")).casefold() == normalized
        ]
    formatter = getattr(service, "format_adapter_snapshot", None)
    details = (
        formatter(adapters)
        if callable(formatter)
        else "\n".join(str(adapter) for adapter in adapters)
    )
    if interface_name and not adapters:
        return _tool_result(
            success=False,
            message=f"Interface not found: {interface_name}",
            details=details
            or "No matching interface was returned by NetworkInterfaceService.",
            data=[],
            status="error",
        )
    return _tool_result(
        success=True,
        message=f"Interface snapshot captured: {len(adapters)} adapter(s).",
        details=details,
        data=[_plain_data(adapter) for adapter in adapters],
    )


def _handle_app_paths(state: Any, _args: dict[str, Any]) -> ToolResult:
    paths = getattr(state, "paths", None)
    if paths is None:
        return _tool_result(
            success=False,
            message="App paths are not available.",
            details="AppState.paths is missing.",
            data=[],
            status="error",
        )
    rows = _app_paths_payload(paths)
    details = "\n".join(f"- {row['name']}: {row['path']}" for row in rows)
    return _tool_result(
        success=True, message=f"App paths: {len(rows)}", details=details, data=rows
    )


def _handle_artifacts_list(state: Any, args: dict[str, Any]) -> ToolResult:
    paths = getattr(state, "paths", None)
    if paths is None:
        return _tool_result(
            success=False,
            message="Artifact roots are not available.",
            details="AppState.paths is missing.",
            data={"roots": [], "artifacts": [], "total_count": 0, "truncated": False},
            status="error",
        )
    limit = _int_arg(args, "limit", default=100, minimum=1, maximum=300)
    include_hidden = _bool_arg(args, "include_hidden", default=False)
    artifacts, roots, total_count = _list_artifacts(
        paths, limit=limit, include_hidden=include_hidden
    )
    details = "\n".join(
        f"- {item['modified_at']} {item['kind']} {item['relative_path']}"
        for item in artifacts[:25]
    )
    if total_count > len(artifacts):
        details = "\n".join(
            part
            for part in (details, f"... {total_count - len(artifacts)} more omitted")
            if part
        )
    return _tool_result(
        success=True,
        message=f"Artifacts: {len(artifacts)}/{total_count}",
        details=details,
        data={
            "roots": roots,
            "artifacts": artifacts,
            "total_count": total_count,
            "truncated": total_count > len(artifacts),
        },
    )


def _handle_set_dns(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "network_interface_service")
    interface_name = _interface_arg(args)
    if "dns_servers" not in args:
        raise NetOpsToolValidationError(
            "dns_servers is required. Use an empty list only to reset DNS to automatic."
        )
    dns_servers = _dns_servers_arg(args)
    return _operation_tool_result(service.set_dns(interface_name, dns_servers))


def _handle_set_dhcp(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "network_interface_service")
    interface_name = _interface_arg(args)
    return _operation_tool_result(service.set_dhcp(interface_name))


def _handle_set_static_ip(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "network_interface_service")
    interface_name = _interface_arg(args)
    ip_address = _ipv4_arg(args, "ip_address")
    prefix = _prefix_arg(args)
    gateway = _ipv4_arg(args, "gateway", required=False)
    dns_servers = _dns_servers_arg(args)
    return _operation_tool_result(
        service.set_static(interface_name, ip_address, prefix, gateway, dns_servers)
    )


def _handle_wifi_status(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "wireless_service")
    info = service.get_wireless_info()
    details = _format_wireless_info(info)
    has_status = any(
        str(getattr(info, field, "") or "").strip()
        for field in ("interface_name", "description", "state", "ssid", "bssid")
    )
    return _tool_result(
        success=has_status,
        message="Wi-Fi status captured."
        if has_status
        else "Wi-Fi status is not available.",
        details=details,
        data=_plain_data(info),
        status="ok" if has_status else "error",
    )


def _handle_wifi_scan_nearby(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "wireless_service")
    duration_seconds = _int_arg(
        args, "duration_seconds", default=20, minimum=5, maximum=120
    )
    interval_seconds = _int_arg(
        args, "interval_seconds", default=5, minimum=2, maximum=30
    )
    # Injected only after public tool arguments pass policy/schema validation.
    cancel_event = args.get("_cancel_event")

    report_method = getattr(service, "scan_nearby_access_points_window", None)
    if callable(report_method):
        report_kwargs = {
            "duration_seconds": duration_seconds,
            "interval_seconds": interval_seconds,
            "include_oui": True,
        }
        if cancel_event is not None:
            report_kwargs["cancel_event"] = cancel_event
        report = report_method(**report_kwargs)
        return _wireless_scan_result(
            report, duration_seconds=duration_seconds, interval_seconds=interval_seconds
        )

    report_method = getattr(service, "scan_nearby_report", None)
    if callable(report_method):
        report_kwargs = {
            "duration_seconds": duration_seconds,
            "interval_seconds": interval_seconds,
        }
        if cancel_event is not None:
            report_kwargs["cancel_event"] = cancel_event
        report = report_method(**report_kwargs)
        return _wireless_scan_result(
            report, duration_seconds=duration_seconds, interval_seconds=interval_seconds
        )

    scan_method = getattr(service, "scan_nearby_access_points", None)
    if not callable(scan_method):
        raise RuntimeError(
            "AppState service is not available: wireless_service.scan_nearby_access_points"
        )

    (
        access_points,
        sample_count,
        actual_duration_seconds,
        cancelled,
        sample_limit_reached,
    ) = _run_wireless_scans(
        scan_method,
        duration_seconds=duration_seconds,
        interval_seconds=interval_seconds,
        cancel_event=cancel_event,
    )
    return _wireless_scan_result(
        access_points,
        duration_seconds=duration_seconds,
        interval_seconds=interval_seconds,
        sample_count=sample_count,
        actual_duration_seconds=actual_duration_seconds,
        cancelled=cancelled,
        sample_limit_reached=sample_limit_reached,
    )


def _handle_oui_lookup(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "oui_service")
    mac_address = _text_arg(args, "mac_address", field_label="mac_address")
    normalized = "".join(ch for ch in mac_address.upper() if ch in "0123456789ABCDEF")
    if len(normalized) < 6:
        raise NetOpsToolValidationError(
            "mac_address must contain at least an OUI prefix."
        )
    record = service.lookup(mac_address)
    if record is None:
        return _tool_result(
            success=False,
            message=f"OUI vendor not found for {mac_address}.",
            details=f"MAC/OUI: {mac_address}",
            data={"mac_address": mac_address, "normalized": normalized},
            status="error",
        )
    data = _plain_data(record)
    return _tool_result(
        success=True,
        message=f"OUI vendor: {getattr(record, 'organization', '-')}",
        details="\n".join(
            [
                f"MAC/OUI: {mac_address}",
                f"Vendor: {getattr(record, 'organization', '-')}",
                f"Prefix: {getattr(record, 'prefix', '-')}/{getattr(record, 'prefix_bits', '-')}",
                f"Registry: {getattr(record, 'registry', '-')}",
            ]
        ),
        data=data,
    )


def _handle_oui_cache_summary(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = getattr(state, "oui_service", None)
    summary = ""
    if service is not None:
        cache_summary = getattr(service, "cache_summary", None)
        if callable(cache_summary):
            try:
                summary = str(cache_summary() or "")
            except Exception as exc:
                return _tool_result(
                    success=False,
                    message="OUI cache summary failed.",
                    details=str(exc),
                    data={"summary": "", "cache": None},
                    status="error",
                )
    cache_path = getattr(getattr(state, "paths", None), "oui_cache", None)
    cache = _path_metadata("oui_cache", cache_path) if cache_path is not None else None
    message = (
        f"OUI cache: {summary}" if summary else "OUI cache summary is not available."
    )
    return _tool_result(
        success=True,
        message=message,
        details=summary,
        data={"summary": summary, "cache": cache},
        status="ok" if summary or cache else "warning",
    )


def _handle_oui_cache_refresh(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "oui_service")
    return _operation_tool_result(service.refresh_cache())


def _handle_inspector_profiles_list(state: Any, args: dict[str, Any]) -> ToolResult:
    limit = _int_arg(args, "limit", default=100, minimum=1, maximum=1000)
    try:
        service = _read_only_inspector_service(state)
        profiles = [
            _plain_data(profile) for profile in service.supported_profile_definitions()
        ]
    except Exception as exc:
        return _tool_result(
            success=False,
            message="Inspector profiles could not be listed.",
            details=str(exc),
            data={"profiles": [], "total_count": 0, "truncated": False},
            status="error",
        )
    total_count = len(profiles)
    rows = profiles[:limit]
    details = "\n".join(
        f"- {item.get('display_name') or item.get('key')}: commands={item.get('command_count', 0)} "
        f"backup={'yes' if item.get('has_backup') else 'no'}"
        for item in rows[:25]
        if isinstance(item, Mapping)
    )
    if total_count > len(rows):
        details = "\n".join(
            part
            for part in (details, f"... {total_count - len(rows)} more omitted")
            if part
        )
    return _tool_result(
        success=True,
        message=f"Inspector profiles: {len(rows)}/{total_count}",
        details=details,
        data={
            "profiles": rows,
            "total_count": total_count,
            "truncated": total_count > len(rows),
        },
    )


def _handle_config_builder_profiles_list(
    state: Any, args: dict[str, Any]
) -> ToolResult:
    limit = _int_arg(args, "limit", default=100, minimum=1, maximum=1000)
    try:
        service = _read_only_config_builder_service(state)
        summaries = [_plain_data(summary) for summary in service.profile_summaries()]
        profiles_dir = getattr(service, "profiles_dir", "")
    except Exception as exc:
        return _tool_result(
            success=False,
            message="Config builder profiles could not be listed.",
            details=str(exc),
            data={"profiles": [], "total_count": 0, "truncated": False},
            status="error",
        )
    total_count = len(summaries)
    rows = summaries[:limit]
    details = "\n".join(
        f"- {item.get('id')}: vendor={item.get('vendor') or '-'} model={item.get('model') or '-'}"
        for item in rows[:25]
        if isinstance(item, Mapping)
    )
    if total_count > len(rows):
        details = "\n".join(
            part
            for part in (details, f"... {total_count - len(rows)} more omitted")
            if part
        )
    return _tool_result(
        success=True,
        message=f"Config builder profiles: {len(rows)}/{total_count}",
        details=details,
        data={
            "profiles_dir": str(profiles_dir),
            "profiles": rows,
            "total_count": total_count,
            "truncated": total_count > len(rows),
        },
    )


def _handle_ip_profiles(state: Any, _args: dict[str, Any]) -> ToolResult:
    profiles = [_plain_data(profile) for profile in getattr(state, "ip_profiles", [])]
    return _tool_result(
        success=True, message=f"IP profiles: {len(profiles)}", data=profiles
    )


def _handle_ftp_profiles(state: Any, _args: dict[str, Any]) -> ToolResult:
    profiles = [_plain_data(profile) for profile in getattr(state, "ftp_profiles", [])]
    return _tool_result(
        success=True, message=f"FTP/SFTP profiles: {len(profiles)}", data=profiles
    )


def _handle_scp_profiles(state: Any, _args: dict[str, Any]) -> ToolResult:
    profiles = [_plain_data(profile) for profile in getattr(state, "scp_profiles", [])]
    return _tool_result(
        success=True, message=f"SCP profiles: {len(profiles)}", data=profiles
    )


def _handle_arp_scan_candidates(state: Any, _args: dict[str, Any]) -> ToolResult:
    interface_service = _required_service(state, "network_interface_service")
    arp_service = _required_service(state, "arp_scan_service")
    adapters = list(interface_service.list_adapters())
    candidates = [
        {"label": label, "cidr": cidr}
        for label, cidr in arp_service.list_candidate_subnets(adapters)
    ]
    details = "\n".join(f"- {item['label']}: {item['cidr']}" for item in candidates)
    return _tool_result(
        success=True,
        message=f"ARP scan candidates: {len(candidates)}",
        details=details,
        data=candidates,
    )


def _handle_arp_scan(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "arp_scan_service")
    subnet = _text_arg(args, "subnet", field_label="subnet")
    timeout_ms = _int_arg(args, "timeout_ms", default=800, minimum=100, maximum=5000)
    max_workers = _int_arg(args, "max_workers", default=64, minimum=1, maximum=128)
    return _operation_tool_result(
        service.run_scan(subnet, timeout_ms=timeout_ms, max_workers=max_workers)
    )


def _handle_iperf_status(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "iperf_service")
    executable, source = service.executable_details()
    version = service.executable_version(executable) if executable else ""
    managed_state = service.managed_install_state()
    details = "\n".join(
        [
            f"Executable: {executable or '-'}",
            f"Source: {source or '-'}",
            f"Version: {version or '-'}",
            f"Managed install supported: {managed_state.get('available')}",
            f"Managed installed: {managed_state.get('installed')}",
            f"Managed update available: {managed_state.get('update_available')}",
        ]
    )
    return _tool_result(
        True,
        "iperf3 is available." if executable else "iperf3 is not available.",
        details=details,
        data={
            "executable": executable,
            "source": source,
            "version": version,
            "managed": managed_state,
        },
        status="ok" if executable else "warning",
    )


def _handle_iperf_client_test(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "iperf_service")
    server = _target_arg(args, "server")
    port = _port_arg(args)
    streams = _int_arg(args, "streams", default=1, minimum=1, maximum=64)
    duration = _int_arg(args, "duration", default=10, minimum=1, maximum=3600)
    reverse = _bool_arg(args, "reverse", default=False)
    udp = _bool_arg(args, "udp", default=False)
    ipv6 = _bool_arg(args, "ipv6", default=False)
    return _operation_tool_result(
        service.run_test(
            "client",
            server,
            port,
            streams,
            duration,
            reverse=reverse,
            udp=udp,
            ipv6=ipv6,
        )
    )


def _handle_public_iperf_cached(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "public_iperf_service")
    return _operation_tool_result(service.load_cached_servers())


def _handle_public_iperf_refresh(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "public_iperf_service")
    force_refresh = _bool_arg(args, "force_refresh", default=False)
    return _operation_tool_result(
        service.fetch_public_servers(force_refresh=force_refresh)
    )


def _handle_ftp_client_runtime(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(service.runtime_support_status(_protocol_arg(args)))


def _handle_ftp_server_runtime(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_server_service")
    return _operation_tool_result(service.runtime_support_status(_protocol_arg(args)))


def _handle_ftp_connect(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.connect(
            _protocol_arg(args),
            _target_arg(args, "host"),
            args.get("port"),
            _text_arg(args, "username", field_label="username"),
            str(args.get("password", "") or ""),
            passive_mode=_bool_arg(args, "passive_mode", default=True),
            timeout_seconds=_int_arg(
                args, "timeout_seconds", default=15, minimum=1, maximum=300
            ),
            remote_path=_remote_path_arg(args, "remote_path", required=False) or "/",
        )
    )


def _handle_ftp_disconnect(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.disconnect(_text_arg(args, "session_id", field_label="session_id"))
    )


def _handle_ftp_list(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.list_directory(
            _text_arg(args, "session_id", field_label="session_id"),
            _remote_path_arg(args, "remote_path", required=False),
        )
    )


def _handle_ftp_upload(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.upload_files(
            _text_arg(args, "session_id", field_label="session_id"),
            _text_list_arg(args, "local_paths", required=True, maximum=64),
            _remote_path_arg(args, "remote_dir", required=False),
        )
    )


def _handle_ftp_download(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.download_files(
            _text_arg(args, "session_id", field_label="session_id"),
            _ftp_remote_entries_arg(args),
            _text_arg(args, "local_dir", field_label="local_dir"),
        )
    )


def _handle_ftp_mkdir(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.make_directory(
            _text_arg(args, "session_id", field_label="session_id"),
            _remote_path_arg(args, "current_dir", required=False),
            _text_arg(args, "folder_name", field_label="folder_name"),
        )
    )


def _handle_ftp_rename(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.rename_path(
            _text_arg(args, "session_id", field_label="session_id"),
            _remote_path_arg(args, "source_path"),
            _text_arg(args, "new_name", field_label="new_name"),
        )
    )


def _handle_ftp_delete(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "ftp_client_service")
    return _operation_tool_result(
        service.delete_entries(
            _text_arg(args, "session_id", field_label="session_id"),
            _ftp_remote_entries_arg(args),
        )
    )


def _handle_scp_client_runtime(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "scp_client_service")
    return _operation_tool_result(service.runtime_support_status())


def _handle_scp_upload(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "scp_client_service")
    return _operation_tool_result(
        service.upload_files(
            _target_arg(args, "host"),
            args.get("port", 22),
            _text_arg(args, "username", field_label="username"),
            str(args.get("password", "") or ""),
            _text_list_arg(args, "local_paths", required=True, maximum=64),
            _remote_path_arg(args, "remote_path"),
            timeout_seconds=_int_arg(
                args, "timeout_seconds", default=15, minimum=1, maximum=300
            ),
        )
    )


def _handle_scp_download(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "scp_client_service")
    return _operation_tool_result(
        service.download_files(
            _target_arg(args, "host"),
            args.get("port", 22),
            _text_arg(args, "username", field_label="username"),
            str(args.get("password", "") or ""),
            _text_list_arg(args, "remote_sources", required=True, maximum=64),
            _text_arg(args, "local_dir", field_label="local_dir"),
            timeout_seconds=_int_arg(
                args, "timeout_seconds", default=15, minimum=1, maximum=300
            ),
        )
    )


def _handle_tftp_runtime(state: Any, _args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "tftp_service")
    return _operation_tool_result(service.runtime_support_status())


def _handle_tftp_upload(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "tftp_service")
    return _operation_tool_result(
        service.upload_file(
            _target_arg(args, "host"),
            args.get("port", 69),
            _text_arg(args, "local_path", field_label="local_path"),
            _remote_path_arg(args, "remote_path"),
            timeout_seconds=_int_arg(
                args, "timeout_seconds", default=5, minimum=1, maximum=120
            ),
            retries=_int_arg(args, "retries", default=3, minimum=0, maximum=20),
        )
    )


def _handle_tftp_download(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "tftp_service")
    return _operation_tool_result(
        service.download_file(
            _target_arg(args, "host"),
            args.get("port", 69),
            _remote_path_arg(args, "remote_path"),
            _text_arg(args, "local_folder", field_label="local_folder"),
            timeout_seconds=_int_arg(
                args, "timeout_seconds", default=5, minimum=1, maximum=120
            ),
            retries=_int_arg(args, "retries", default=3, minimum=0, maximum=20),
        )
    )


def _handle_update_check(state: Any, args: dict[str, Any]) -> ToolResult:
    service = _required_service(state, "update_service")
    config = (
        getattr(state, "app_config", {}).get("update", {})
        if isinstance(getattr(state, "app_config", {}), dict)
        else {}
    )
    current_version = str(
        args.get("current_version", config.get("current_version", "")) or ""
    )
    repo = str(args.get("repo", config.get("repo", "")) or "")
    asset_pattern = str(
        args.get("asset_pattern", config.get("asset_pattern", "")) or ""
    )
    if not current_version:
        raise NetOpsToolValidationError("current_version is required.")
    if not repo:
        raise NetOpsToolValidationError("repo is required.")
    if not asset_pattern:
        raise NetOpsToolValidationError("asset_pattern is required.")
    result = service.check_for_updates(current_version, repo, asset_pattern)
    return _tool_result(
        True,
        "Update check completed.",
        details=str(result),
        data=_plain_data(result),
    )


def _format_ping_result(result: Any) -> str:
    lines = [
        f"Target: {getattr(result, 'target', '-')}",
        f"Status: {getattr(result, 'status', '-')}",
        f"Received/Sent: {getattr(result, 'received', 0)}/{getattr(result, 'sent', 0)}",
        f"Packet loss: {float(getattr(result, 'packet_loss', 0) or 0):.0f}%",
    ]
    avg_rtt = getattr(result, "avg_rtt", None)
    if avg_rtt is not None:
        lines.append(
            "RTT min/avg/max: "
            f"{getattr(result, 'min_rtt', '-')}/"
            f"{avg_rtt}/"
            f"{getattr(result, 'max_rtt', '-')} ms"
        )
    error = str(getattr(result, "error", "") or "").strip()
    if error:
        lines.append(f"Error: {error}")
    return "\n".join(lines)


def _format_tcp_check_result(result: Any) -> str:
    lines = [
        f"Target: {getattr(result, 'target', '-')}:{getattr(result, 'port', '-')}",
        f"Status: {getattr(result, 'status', '-')}",
        f"Successful/Sent: {getattr(result, 'successful', 0)}/{getattr(result, 'sent', 0)}",
        f"Packet loss: {float(getattr(result, 'packet_loss', 0) or 0):.0f}%",
    ]
    response_ms = getattr(result, "response_ms", None)
    if response_ms is not None:
        lines.append(f"Average response: {response_ms} ms")
    error = str(getattr(result, "error", "") or "").strip()
    if error:
        lines.append(f"Error: {error}")
    return "\n".join(lines)


def _format_wireless_info(info: Any) -> str:
    return "\n".join(
        [
            f"Interface: {getattr(info, 'interface_name', '') or '-'}",
            f"Description: {getattr(info, 'description', '') or '-'}",
            f"State: {getattr(info, 'state', '') or '-'}",
            f"SSID: {getattr(info, 'ssid', '') or '-'}",
            f"BSSID: {getattr(info, 'bssid', '') or '-'}",
            f"Radio: {getattr(info, 'radio_type', '') or '-'}",
            f"Channel/Band: {getattr(info, 'channel', '') or '-'} / {getattr(info, 'band', '') or '-'}",
            f"Signal: {getattr(info, 'signal_text', '-')}",
            (
                "Receive/Transmit: "
                f"{getattr(info, 'receive_rate_mbps', '') or '-'} / "
                f"{getattr(info, 'transmit_rate_mbps', '') or '-'} Mbps"
            ),
        ]
    )


def _run_wireless_scans(
    scan_method: Any,
    *,
    duration_seconds: int,
    interval_seconds: int,
    cancel_event: Any = None,
) -> tuple[list[Any], int, float, bool, bool]:
    requested_scan_count = max(
        1, 1 + max(0, duration_seconds - 1) // max(1, interval_seconds)
    )
    scan_count = min(25, requested_scan_count)
    found: dict[str, Any] = {}
    completed_scans = 0
    cancelled = False
    started_at = time.monotonic()
    deadline = started_at + duration_seconds
    for index in range(scan_count):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        scheduled_at = started_at + (index * interval_seconds)
        now = time.monotonic()
        if index > 0 and now >= deadline:
            break
        wait_seconds = scheduled_at - now
        if wait_seconds > 0:
            if cancel_event is not None:
                if cancel_event.wait(wait_seconds):
                    cancelled = True
                    break
            else:
                time.sleep(wait_seconds)
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        if index > 0 and time.monotonic() >= deadline:
            break
        access_points = scan_method()
        completed_scans += 1
        for access_point in _extract_wireless_access_points(access_points):
            key = _wireless_access_point_key(access_point)
            if key not in found or _wireless_signal(access_point) > _wireless_signal(
                found[key]
            ):
                found[key] = access_point
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
    sample_limit_reached = (
        not cancelled
        and requested_scan_count > scan_count
        and completed_scans == scan_count
    )
    return (
        list(found.values()),
        completed_scans,
        time.monotonic() - started_at,
        cancelled,
        sample_limit_reached,
    )


def _wireless_scan_result(
    report_or_access_points: Any,
    *,
    duration_seconds: int,
    interval_seconds: int,
    sample_count: int | None = None,
    actual_duration_seconds: float | None = None,
    cancelled: bool | None = None,
    sample_limit_reached: bool | None = None,
) -> ToolResult:
    access_points = _extract_wireless_access_points(report_or_access_points)
    channel_summaries = _extract_wireless_channel_summaries(report_or_access_points)
    unstable_access_points = _extract_wireless_unstable_access_points(
        report_or_access_points
    )
    errors = list(getattr(report_or_access_points, "errors", []) or [])
    access_points = sorted(
        access_points,
        key=lambda item: (
            str(getattr(item, "ssid", "") or "").casefold(),
            -_wireless_signal(item),
            str(getattr(item, "bssid", "") or "").casefold(),
        ),
    )
    requested_duration = int(
        getattr(report_or_access_points, "duration_seconds", duration_seconds)
        or duration_seconds
    )
    reported_interval = int(
        getattr(report_or_access_points, "interval_seconds", interval_seconds)
        or interval_seconds
    )
    if sample_count is None:
        sample_count = getattr(report_or_access_points, "sample_count", None)
    if actual_duration_seconds is None:
        actual_duration_seconds = getattr(
            report_or_access_points, "actual_duration_seconds", None
        )
    if cancelled is None:
        cancelled = bool(getattr(report_or_access_points, "cancelled", False))
    if sample_limit_reached is None:
        sample_limit_reached = bool(
            getattr(report_or_access_points, "sample_limit_reached", False)
        )

    data = {
        "duration_seconds": requested_duration,
        "interval_seconds": reported_interval,
        "access_points": [_plain_data(access_point) for access_point in access_points],
    }
    if sample_count is not None:
        data["sample_count"] = int(sample_count or 0)
    if actual_duration_seconds is not None:
        data["actual_duration_seconds"] = float(actual_duration_seconds)
    if cancelled:
        data["cancelled"] = True
    if sample_limit_reached:
        data["sample_limit_reached"] = True
    if hasattr(report_or_access_points, "observed_access_points"):
        data["report"] = _plain_data(report_or_access_points)
    if channel_summaries:
        data["channel_summaries"] = [
            _plain_data(summary) for summary in channel_summaries
        ]
    if unstable_access_points:
        data["unstable_access_points"] = [
            _plain_data(access_point) for access_point in unstable_access_points
        ]
    if errors:
        data["errors"] = [str(error) for error in errors]
    measured_text = (
        f"{float(actual_duration_seconds):.1f}s"
        if actual_duration_seconds is not None
        else "unavailable"
    )
    detail_sections: list[str] = [
        "\n".join(
            [
                f"Requested window: {requested_duration}s / interval {reported_interval}s",
                f"Measured elapsed: {measured_text}",
                f"Samples: {int(sample_count or 0) if sample_count is not None else '-'}",
                *(["Scan cancelled: yes"] if cancelled else []),
                *(["Sample limit reached: yes"] if sample_limit_reached else []),
                f"Observed AP/BSSID: {len(access_points)}",
            ]
        )
    ]
    if access_points:
        detail_sections.append(
            "\n".join(
                _format_wireless_access_point(access_point)
                for access_point in access_points
            )
        )
    if channel_summaries:
        detail_sections.append(
            "Channel summary\n"
            + "\n".join(
                _format_wireless_channel_summary(summary)
                for summary in channel_summaries
            )
        )
    if unstable_access_points:
        detail_sections.append(
            "Unstable APs\n"
            + "\n".join(
                _format_wireless_access_point(access_point)
                for access_point in unstable_access_points
            )
        )
    if errors:
        detail_sections.append("Errors\n" + "\n".join(f"- {error}" for error in errors))
    details = "\n\n".join(section for section in detail_sections if section.strip())
    success = not cancelled and not (errors and not access_points)
    status = (
        "ok" if success and not errors else ("warning" if access_points else "error")
    )
    if cancelled:
        message = f"Nearby Wi-Fi scan cancelled: {len(access_points)} access point(s) observed."
    else:
        message = (
            f"Nearby Wi-Fi scan completed: {len(access_points)} access point(s) found."
        )
    return _tool_result(
        success=success,
        message=message,
        details=details,
        data=data,
        status=status,
    )


def _extract_wireless_access_points(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in (
            "observed_access_points",
            "access_points",
            "nearby_access_points",
            "aps",
            "networks",
            "results",
            "payload",
            "data",
        ):
            if key in value:
                return _extract_wireless_access_points(value[key])
        return []
    for attr_name in (
        "observed_access_points",
        "access_points",
        "nearby_access_points",
        "aps",
        "networks",
        "results",
        "payload",
        "data",
    ):
        if hasattr(value, attr_name):
            return _extract_wireless_access_points(getattr(value, attr_name))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _extract_wireless_channel_summaries(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        raw_value = value.get("channel_summaries", [])
        return (
            list(raw_value)
            if isinstance(raw_value, Sequence)
            and not isinstance(raw_value, (str, bytes, bytearray))
            else []
        )
    raw_value = getattr(value, "channel_summaries", [])
    return (
        list(raw_value)
        if isinstance(raw_value, Sequence)
        and not isinstance(raw_value, (str, bytes, bytearray))
        else []
    )


def _extract_wireless_unstable_access_points(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        raw_value = value.get("unstable_access_points", [])
        return (
            list(raw_value)
            if isinstance(raw_value, Sequence)
            and not isinstance(raw_value, (str, bytes, bytearray))
            else []
        )
    raw_value = getattr(value, "unstable_access_points", [])
    return (
        list(raw_value)
        if isinstance(raw_value, Sequence)
        and not isinstance(raw_value, (str, bytes, bytearray))
        else []
    )


def _wireless_access_point_key(access_point: Any) -> str:
    bssid = str(getattr(access_point, "bssid", "") or "").strip().casefold()
    ssid = str(getattr(access_point, "ssid", "") or "").strip().casefold()
    channel = str(getattr(access_point, "channel", "") or "").strip().casefold()
    return "|".join(part for part in (bssid, ssid, channel) if part) or repr(
        access_point
    )


def _wireless_signal(access_point: Any) -> int:
    raw_value = getattr(access_point, "signal_percent", None)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return -1


def _format_wireless_access_point(access_point: Any) -> str:
    fields = [
        str(getattr(access_point, "ssid", "") or "<hidden>"),
        str(getattr(access_point, "bssid", "") or "-"),
        f"signal {getattr(access_point, 'signal_text', None) or str(_wireless_signal(access_point)) + '%'}",
    ]
    channel = str(getattr(access_point, "channel", "") or "").strip()
    band = str(getattr(access_point, "band", "") or "").strip()
    if channel or band:
        fields.append(f"channel {channel or '-'} / {band or '-'}")
    auth = str(getattr(access_point, "authentication", "") or "").strip()
    encryption = str(getattr(access_point, "encryption", "") or "").strip()
    if auth or encryption:
        fields.append(f"security {auth or '-'} / {encryption or '-'}")
    vendor = str(getattr(access_point, "vendor", "") or "").strip()
    if vendor:
        fields.append(f"vendor {vendor}")
    sample_count = getattr(access_point, "sample_count", None)
    if sample_count is not None:
        fields.append(f"samples {sample_count}")
    if bool(getattr(access_point, "unstable", False)):
        fields.append("unstable")
    return "- " + " | ".join(fields)


def _format_wireless_channel_summary(summary: Any) -> str:
    fields = [
        str(getattr(summary, "channel", "") or "-"),
        str(getattr(summary, "band", "") or "-"),
        f"APs {int(getattr(summary, 'access_point_count', 0) or 0)}",
        f"observations {int(getattr(summary, 'observation_count', 0) or 0)}",
    ]
    utilization = getattr(summary, "average_channel_utilization_percent", None)
    if utilization is not None:
        fields.append(f"utilization {float(utilization):.1f}%")
    avg_signal = getattr(summary, "average_signal_percent", None)
    if avg_signal is not None:
        fields.append(f"avg signal {float(avg_signal):.1f}%")
    return "- " + " | ".join(fields)


NETOPS_TOOL_SPECS = (
    (
        _descriptor(
            name="ping",
            display_name="Ping",
            description="Ping one host or IP address through PingService.quick_ping.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "target": {
                        "type": "string",
                        "description": "Hostname or IP address to ping.",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 4,
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 15000,
                        "default": 4000,
                    },
                    "continuous": {"type": "boolean", "default": False},
                },
                required=("target",),
            ),
            timeout_seconds=45,
            tags=("diagnostic", "icmp"),
            aliases=("net.ping",),
        ),
        _handle_ping,
    ),
    (
        _descriptor(
            name="ping_batch",
            display_name="Ping Batch",
            description="Ping multiple hosts or IP addresses through PingService.run_multi_ping.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 2,
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 10000,
                        "default": 4000,
                    },
                    "max_workers": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 16,
                        "default": 8,
                    },
                    "continuous": {"type": "boolean", "default": False},
                },
                required=("targets",),
            ),
            timeout_seconds=120,
            tags=("diagnostic", "icmp", "batch"),
        ),
        _handle_ping_batch,
    ),
    (
        _descriptor(
            name="external_ping",
            display_name="External Ping",
            description="Ping a small set of public connectivity targets using PingService.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 6,
                        "default": list(DEFAULT_EXTERNAL_PING_TARGETS),
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 2,
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 15000,
                        "default": 4000,
                    },
                    "continuous": {"type": "boolean", "default": False},
                }
            ),
            timeout_seconds=60,
            tags=("diagnostic", "internet", "icmp"),
            aliases=("net.external_ping",),
        ),
        _handle_external_ping,
    ),
    (
        _descriptor(
            name="tcp_check",
            display_name="TCP Check",
            description="Check TCP connectivity to one target and port through TcpCheckService.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "target": {"type": "string"},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 2,
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 15000,
                        "default": 4000,
                    },
                    "continuous": {"type": "boolean", "default": False},
                },
                required=("target", "port"),
            ),
            timeout_seconds=60,
            tags=("diagnostic", "tcp"),
            aliases=("net.tcp_check",),
        ),
        _handle_tcp_check,
    ),
    (
        _descriptor(
            name="tcp_batch",
            display_name="TCP Batch",
            description="Check TCP connectivity for multiple target/port combinations through TcpCheckService.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                    "ports": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "minItems": 1,
                        "maxItems": 16,
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 2,
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 500,
                        "maximum": 10000,
                        "default": 4000,
                    },
                    "max_workers": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 32,
                        "default": 16,
                    },
                    "continuous": {"type": "boolean", "default": False},
                },
                required=("targets", "ports"),
            ),
            timeout_seconds=180,
            tags=("diagnostic", "tcp", "batch"),
        ),
        _handle_tcp_batch,
    ),
    (
        _descriptor(
            name="subnet_calculate",
            display_name="Subnet Calculate",
            description="Calculate network, mask, host range, and host counts for a CIDR subnet.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "cidr": {
                        "type": "string",
                        "description": "CIDR such as 192.168.1.0/24.",
                        "default": "",
                    },
                    "ip_address": {
                        "type": "string",
                        "description": "IP address when cidr is not supplied.",
                        "default": "",
                    },
                    "prefix": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 128,
                        "default": 24,
                    },
                    "include_hosts": {"type": "boolean", "default": False},
                    "max_hosts": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 256,
                        "default": 64,
                    },
                }
            ),
            timeout_seconds=10,
            tags=("diagnostic", "subnet", "calculator", "local"),
        ),
        _handle_subnet_calculate,
    ),
    (
        _descriptor(
            name="dns_lookup",
            display_name="DNS Lookup",
            description="Resolve DNS records through DnsService.lookup.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "query": {"type": "string"},
                    "record_type": {
                        "type": "string",
                        "enum": list(DNS_RECORD_TYPES),
                        "default": "A",
                    },
                    "server": {
                        "type": "string",
                        "description": "Optional DNS server.",
                        "default": "",
                    },
                },
                required=("query",),
            ),
            timeout_seconds=30,
            tags=("diagnostic", "dns"),
            aliases=("net.dns.lookup",),
        ),
        _handle_dns_lookup,
    ),
    (
        _descriptor(
            name="dns_flush_cache",
            display_name="Flush DNS Cache",
            description="Clear the local Windows DNS client cache through DnsService.flush_dns_cache.",
            permission=PermissionClass.WRITE_SYSTEM,
            input_schema=_object_schema({}),
            risk_level="medium",
            admin_required=True,
            approval_required=True,
            impact="Clears cached DNS responses on this Windows machine; new lookups may hit configured DNS servers again.",
            reversibility="The DNS cache repopulates automatically as applications perform DNS lookups.",
            timeout_seconds=30,
            tags=("mutation", "dns", "cache", "local"),
        ),
        _handle_dns_flush_cache,
    ),
    (
        _descriptor(
            name="public_ip",
            display_name="Public IP",
            description="Check the current public IP address through PublicIpService.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 5,
                    },
                }
            ),
            timeout_seconds=35,
            tags=("diagnostic", "internet"),
            aliases=("net.public_ip",),
        ),
        _handle_public_ip,
    ),
    (
        _descriptor(
            name="tracert",
            display_name="Tracert",
            description="Trace the route to one host through TraceService.run_tracert.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "target": {"type": "string"},
                    "resolve_names": {"type": "boolean", "default": True},
                },
                required=("target",),
            ),
            timeout_seconds=180,
            tags=("diagnostic", "route", "icmp"),
        ),
        _handle_tracert,
    ),
    (
        _descriptor(
            name="pathping",
            display_name="Pathping",
            description="Run pathping to one host through TraceService.run_pathping.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "target": {"type": "string"},
                    "resolve_names": {"type": "boolean", "default": True},
                },
                required=("target",),
            ),
            timeout_seconds=900,
            tags=("diagnostic", "route", "icmp"),
        ),
        _handle_pathping,
    ),
    (
        _descriptor(
            name="ipconfig",
            display_name="IP Configuration",
            description="Return local ipconfig /all diagnostics through TraceService.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            timeout_seconds=65,
            tags=("diagnostic", "local"),
            aliases=("net.ipconfig.read",),
        ),
        _handle_ipconfig,
    ),
    (
        _descriptor(
            name="route_print",
            display_name="Route Table",
            description="Return the local IPv4/IPv6 route table through TraceService.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            timeout_seconds=35,
            tags=("diagnostic", "local", "route"),
            aliases=("net.route.print",),
        ),
        _handle_route_print,
    ),
    (
        _descriptor(
            name="arp_table",
            display_name="ARP Table",
            description="Return the local ARP table through TraceService.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            timeout_seconds=35,
            tags=("diagnostic", "local", "arp"),
            aliases=("net.arp.table",),
        ),
        _handle_arp_table,
    ),
    (
        _descriptor(
            name="interface_snapshot",
            display_name="Interface Snapshot",
            description="List Windows network adapter state through NetworkInterfaceService.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "interface_name": {
                        "type": "string",
                        "description": "Optional exact adapter name.",
                        "default": "",
                    },
                }
            ),
            timeout_seconds=30,
            tags=("diagnostic", "local", "adapter"),
            aliases=("net.interface.snapshot", "net.adapters.list"),
        ),
        _handle_interface_snapshot,
    ),
    (
        _descriptor(
            name="app_paths",
            display_name="App Paths",
            description="List NetOps Suite runtime, config, log, export, and cache paths from AppState.paths.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            timeout_seconds=10,
            tags=("app", "paths", "local", "read"),
        ),
        _handle_app_paths,
    ),
    (
        _descriptor(
            name="artifacts_list",
            display_name="Artifacts List",
            description="List recent local NetOps Suite logs, exports, inspector runs, and config builder artifacts.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 100,
                    },
                    "include_hidden": {"type": "boolean", "default": False},
                }
            ),
            timeout_seconds=30,
            tags=("app", "artifacts", "local", "read"),
        ),
        _handle_artifacts_list,
    ),
    (
        _descriptor(
            name="set_dns",
            display_name="Set DNS",
            description="Set or reset IPv4 DNS servers on one adapter through NetworkInterfaceService.set_dns.",
            permission=PermissionClass.WRITE_SYSTEM,
            input_schema=_object_schema(
                {
                    "interface_name": {"type": "string"},
                    "dns_servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                        "description": "IPv4 DNS servers. Empty list resets DNS to automatic.",
                    },
                },
                required=("interface_name", "dns_servers"),
            ),
            risk_level="medium",
            admin_required=True,
            approval_required=True,
            impact="Changes DNS resolution for the selected Windows network adapter.",
            reversibility="Run set_dns with an empty dns_servers list or run set_dhcp to reset adapter DNS.",
            timeout_seconds=30,
            tags=("mutation", "dns", "adapter"),
            aliases=("net.interface.set_dns",),
        ),
        _handle_set_dns,
    ),
    (
        _descriptor(
            name="set_dhcp",
            display_name="Set DHCP",
            description="Switch one adapter to DHCP addressing and DNS through NetworkInterfaceService.set_dhcp.",
            permission=PermissionClass.WRITE_SYSTEM,
            input_schema=_object_schema(
                {
                    "interface_name": {"type": "string"},
                },
                required=("interface_name",),
            ),
            risk_level="high",
            admin_required=True,
            approval_required=True,
            impact="Changes IPv4 addressing and DNS for the selected adapter and may briefly interrupt connectivity.",
            reversibility="Reapply a known static profile or run set_static_ip with the previous values.",
            timeout_seconds=75,
            tags=("mutation", "dhcp", "adapter"),
            aliases=("net.interface.set_dhcp",),
        ),
        _handle_set_dhcp,
    ),
    (
        _descriptor(
            name="set_static_ip",
            display_name="Set Static IP",
            description="Apply static IPv4 settings through NetworkInterfaceService.set_static.",
            permission=PermissionClass.WRITE_SYSTEM,
            input_schema=_object_schema(
                {
                    "interface_name": {"type": "string"},
                    "ip_address": {"type": "string"},
                    "prefix": {"type": "integer", "minimum": 1, "maximum": 32},
                    "gateway": {"type": "string", "default": ""},
                    "dns_servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                        "default": [],
                    },
                },
                required=("interface_name", "ip_address", "prefix"),
            ),
            risk_level="high",
            admin_required=True,
            approval_required=True,
            impact="Changes IPv4 addressing for the selected adapter and can interrupt local or remote connectivity.",
            reversibility="Run set_dhcp or restore the previous static IP, gateway, and DNS values.",
            timeout_seconds=75,
            tags=("mutation", "static-ip", "adapter"),
            aliases=("net.interface.set_static_ip",),
        ),
        _handle_set_static_ip,
    ),
    (
        _descriptor(
            name="wifi_status",
            display_name="Wi-Fi Status",
            description="Return current Wi-Fi interface status through WirelessService.get_wireless_info.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            timeout_seconds=20,
            tags=("diagnostic", "wifi", "local"),
            aliases=("wifi.status", "wireless_status"),
        ),
        _handle_wifi_status,
    ),
    (
        _descriptor(
            name="wifi_scan_nearby",
            display_name="Nearby Wi-Fi Scan",
            description="Scan nearby Wi-Fi access points through WirelessService.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "duration_seconds": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 120,
                        "default": 20,
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "minimum": 2,
                        "maximum": 30,
                        "default": 5,
                    },
                }
            ),
            risk_level="low",
            impact="Requests Wi-Fi scan refreshes and reads nearby SSID/BSSID signal metadata.",
            reversibility="Read-only diagnostic; scan observations are transient.",
            timeout_seconds=150,
            tags=("diagnostic", "wifi", "wireless", "scan", "local"),
            aliases=("wifi.scan_nearby", "wireless_scan"),
        ),
        _handle_wifi_scan_nearby,
    ),
    (
        _descriptor(
            name="oui_lookup",
            display_name="OUI Lookup",
            description="Look up a MAC address vendor through the local OuiService cache.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "mac_address": {"type": "string"},
                },
                required=("mac_address",),
            ),
            timeout_seconds=15,
            tags=("diagnostic", "oui", "mac"),
            aliases=("oui.lookup",),
        ),
        _handle_oui_lookup,
    ),
    (
        _descriptor(
            name="oui_cache_summary",
            display_name="OUI Cache Summary",
            description="Read the local OUI cache summary and cache file metadata.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            timeout_seconds=10,
            tags=("diagnostic", "oui", "cache", "local", "read"),
        ),
        _handle_oui_cache_summary,
    ),
    (
        _descriptor(
            name="oui_cache_refresh",
            display_name="Refresh OUI Cache",
            description="Download IEEE OUI registries and rewrite the local OUI vendor cache.",
            permission=PermissionClass.WRITE_LOCAL,
            input_schema=_object_schema({}),
            risk_level="medium",
            approval_required=True,
            impact="Downloads public IEEE registry files and replaces the local OUI cache used for MAC vendor lookup.",
            reversibility="The cache can be refreshed again; existing lookup behavior changes to the newly downloaded registry data.",
            timeout_seconds=120,
            tags=("mutation", "oui", "cache", "local"),
        ),
        _handle_oui_cache_refresh,
    ),
    (
        _descriptor(
            name="inspector_profiles_list",
            display_name="Inspector Profiles",
            description="List supported NetOps inspector vendor/OS profiles without running inspections.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 100,
                    },
                }
            ),
            timeout_seconds=30,
            tags=("inspector", "profile", "local", "read"),
        ),
        _handle_inspector_profiles_list,
    ),
    (
        _descriptor(
            name="config_builder_profiles_list",
            display_name="Config Builder Profiles",
            description="List available CLI config builder profiles and their variables/blocks.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 100,
                    },
                }
            ),
            timeout_seconds=30,
            tags=("config-builder", "profile", "local", "read"),
        ),
        _handle_config_builder_profiles_list,
    ),
)

NETOPS_TOOL_SPECS += (
    (
        _descriptor(
            name="ip_profiles",
            display_name="IP Profiles",
            description="List saved IP configuration profiles.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("profile", "read"),
        ),
        _handle_ip_profiles,
    ),
    (
        _descriptor(
            name="ftp_profiles",
            display_name="FTP Profiles",
            description="List saved FTP/SFTP connection profiles without passwords.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("profile", "file-transfer", "read"),
        ),
        _handle_ftp_profiles,
    ),
    (
        _descriptor(
            name="scp_profiles",
            display_name="SCP Profiles",
            description="List saved SCP connection profiles without passwords.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("profile", "file-transfer", "read"),
        ),
        _handle_scp_profiles,
    ),
    (
        _descriptor(
            name="arp_scan_candidates",
            display_name="ARP Scan Candidates",
            description="List local adapter subnets that can be scanned by ARP scan.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("diagnostic", "arp", "local"),
        ),
        _handle_arp_scan_candidates,
    ),
    (
        _descriptor(
            name="arp_scan",
            display_name="ARP Scan",
            description="Scan one IPv4 CIDR using the existing ArpScanService.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "subnet": {"type": "string"},
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 5000,
                        "default": 800,
                    },
                    "max_workers": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 128,
                        "default": 64,
                    },
                },
                required=("subnet",),
            ),
            risk_level="medium",
            impact="Sends ICMP probes across the requested subnet and reads the local ARP table.",
            timeout_seconds=300,
            tags=("diagnostic", "arp", "scan"),
        ),
        _handle_arp_scan,
    ),
    (
        _descriptor(
            name="iperf_status",
            display_name="iperf3 Status",
            description="Check local iperf3 availability, version, source, and managed install state.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("diagnostic", "iperf", "local"),
        ),
        _handle_iperf_status,
    ),
    (
        _descriptor(
            name="iperf_client_test",
            display_name="iperf3 Client Test",
            description="Run an iperf3 client test against a specified server.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "server": {"type": "string"},
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "default": 5201,
                    },
                    "streams": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 64,
                        "default": 1,
                    },
                    "duration": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 3600,
                        "default": 10,
                    },
                    "reverse": {"type": "boolean", "default": False},
                    "udp": {"type": "boolean", "default": False},
                    "ipv6": {"type": "boolean", "default": False},
                },
                required=("server", "port"),
            ),
            risk_level="medium",
            impact="Generates bandwidth test traffic to the selected iperf3 server.",
            timeout_seconds=3700,
            tags=("diagnostic", "iperf", "bandwidth"),
        ),
        _handle_iperf_client_test,
    ),
    (
        _descriptor(
            name="public_iperf_cached",
            display_name="Cached Public iperf Servers",
            description="Read the cached public iperf3 server list.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("diagnostic", "iperf", "cache"),
        ),
        _handle_public_iperf_cached,
    ),
    (
        _descriptor(
            name="public_iperf_refresh",
            display_name="Refresh Public iperf Servers",
            description="Refresh and cache the public iperf3 server list.",
            permission=PermissionClass.WRITE_LOCAL,
            input_schema=_object_schema(
                {"force_refresh": {"type": "boolean", "default": False}}
            ),
            risk_level="low",
            approval_required=True,
            impact="Downloads the public iperf3 server list and updates the local cache file.",
            reversibility="Delete or refresh the public iperf cache.",
            timeout_seconds=45,
            tags=("diagnostic", "iperf", "cache", "mutation"),
        ),
        _handle_public_iperf_refresh,
    ),
    (
        _descriptor(
            name="ftp_client_runtime",
            display_name="FTP Client Runtime",
            description="Check FTP/FTPS/SFTP client runtime support.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "protocol": {
                        "type": "string",
                        "enum": ["ftp", "ftps", "sftp"],
                        "default": "ftp",
                    }
                }
            ),
            tags=("file-transfer", "runtime", "read"),
        ),
        _handle_ftp_client_runtime,
    ),
    (
        _descriptor(
            name="ftp_server_runtime",
            display_name="FTP Server Runtime",
            description="Check FTP/FTPS/SFTP server runtime support.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {
                    "protocol": {
                        "type": "string",
                        "enum": ["ftp", "ftps", "sftp"],
                        "default": "ftp",
                    }
                }
            ),
            tags=("file-transfer", "runtime", "read"),
        ),
        _handle_ftp_server_runtime,
    ),
    (
        _descriptor(
            name="ftp_connect",
            display_name="FTP Connect",
            description="Open an FTP/FTPS/SFTP client session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "protocol": {
                        "type": "string",
                        "enum": ["ftp", "ftps", "sftp"],
                        "default": "ftp",
                    },
                    "host": {"type": "string"},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "passive_mode": {"type": "boolean", "default": True},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 15,
                    },
                    "remote_path": {"type": "string", "default": "/"},
                },
                required=("protocol", "host", "username", "password"),
            ),
            risk_level="medium",
            approval_required=True,
            impact="Connects to a remote FTP/FTPS/SFTP server and stores a temporary in-memory session.",
            reversibility="Run ftp_disconnect or close the app to drop the temporary session.",
            timeout_seconds=320,
            tags=("file-transfer", "remote", "session"),
        ),
        _handle_ftp_connect,
    ),
    (
        _descriptor(
            name="ftp_disconnect",
            display_name="FTP Disconnect",
            description="Close an existing FTP/FTPS/SFTP client session.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema(
                {"session_id": {"type": "string"}}, required=("session_id",)
            ),
            tags=("file-transfer", "session"),
        ),
        _handle_ftp_disconnect,
    ),
    (
        _descriptor(
            name="ftp_list",
            display_name="FTP List Directory",
            description="List a directory in an existing FTP/FTPS/SFTP session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "session_id": {"type": "string"},
                    "remote_path": {"type": "string", "default": ""},
                },
                required=("session_id",),
            ),
            risk_level="low",
            approval_required=True,
            impact="Reads remote directory metadata from an existing transfer session.",
            timeout_seconds=120,
            tags=("file-transfer", "remote", "read"),
        ),
        _handle_ftp_list,
    ),
    (
        _descriptor(
            name="ftp_upload",
            display_name="FTP Upload",
            description="Upload local files through an existing FTP/FTPS/SFTP session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "session_id": {"type": "string"},
                    "local_paths": {"type": "array", "items": {"type": "string"}},
                    "remote_dir": {"type": "string", "default": ""},
                },
                required=("session_id", "local_paths"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Reads local files and writes them to a remote server.",
            timeout_seconds=3600,
            tags=("file-transfer", "remote", "upload", "mutation"),
        ),
        _handle_ftp_upload,
    ),
    (
        _descriptor(
            name="ftp_download",
            display_name="FTP Download",
            description="Download remote files through an existing FTP/FTPS/SFTP session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "session_id": {"type": "string"},
                    "remote_paths": {"type": "array", "items": {"type": "string"}},
                    "local_dir": {"type": "string"},
                },
                required=("session_id", "remote_paths", "local_dir"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Reads remote files and writes them into the selected local folder.",
            timeout_seconds=3600,
            tags=("file-transfer", "remote", "download", "mutation"),
        ),
        _handle_ftp_download,
    ),
    (
        _descriptor(
            name="ftp_mkdir",
            display_name="FTP Make Directory",
            description="Create a remote folder in an existing FTP/FTPS/SFTP session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "session_id": {"type": "string"},
                    "current_dir": {"type": "string", "default": ""},
                    "folder_name": {"type": "string"},
                },
                required=("session_id", "folder_name"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Creates a folder on the remote server.",
            tags=("file-transfer", "remote", "mutation"),
        ),
        _handle_ftp_mkdir,
    ),
    (
        _descriptor(
            name="ftp_rename",
            display_name="FTP Rename",
            description="Rename a remote path in an existing FTP/FTPS/SFTP session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "session_id": {"type": "string"},
                    "source_path": {"type": "string"},
                    "new_name": {"type": "string"},
                },
                required=("session_id", "source_path", "new_name"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Renames a file or folder on the remote server.",
            tags=("file-transfer", "remote", "mutation"),
        ),
        _handle_ftp_rename,
    ),
    (
        _descriptor(
            name="ftp_delete",
            display_name="FTP Delete",
            description="Delete remote files or folders in an existing FTP/FTPS/SFTP session.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "session_id": {"type": "string"},
                    "remote_paths": {"type": "array", "items": {"type": "string"}},
                },
                required=("session_id", "remote_paths"),
            ),
            risk_level="critical",
            approval_required=True,
            impact="Deletes selected paths from the remote server.",
            reversibility="Only recoverable if the remote server has backups or recycle-bin semantics.",
            tags=("file-transfer", "remote", "delete", "mutation"),
        ),
        _handle_ftp_delete,
    ),
    (
        _descriptor(
            name="scp_client_runtime",
            display_name="SCP Client Runtime",
            description="Check SCP client runtime support.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("file-transfer", "runtime", "read"),
        ),
        _handle_scp_client_runtime,
    ),
    (
        _descriptor(
            name="scp_upload",
            display_name="SCP Upload",
            description="Upload local files to a remote host with SCP.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "host": {"type": "string"},
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "default": 22,
                    },
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "local_paths": {"type": "array", "items": {"type": "string"}},
                    "remote_path": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 15,
                    },
                },
                required=("host", "username", "password", "local_paths", "remote_path"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Connects over SSH/SCP, reads local files, and writes to the remote host.",
            tags=("file-transfer", "remote", "scp", "upload", "mutation"),
        ),
        _handle_scp_upload,
    ),
    (
        _descriptor(
            name="scp_download",
            display_name="SCP Download",
            description="Download remote files from a host with SCP.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "host": {"type": "string"},
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "default": 22,
                    },
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "remote_sources": {"type": "array", "items": {"type": "string"}},
                    "local_dir": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 15,
                    },
                },
                required=(
                    "host",
                    "username",
                    "password",
                    "remote_sources",
                    "local_dir",
                ),
            ),
            risk_level="high",
            approval_required=True,
            impact="Connects over SSH/SCP, reads remote files, and writes them locally.",
            tags=("file-transfer", "remote", "scp", "download", "mutation"),
        ),
        _handle_scp_download,
    ),
    (
        _descriptor(
            name="tftp_runtime",
            display_name="TFTP Runtime",
            description="Check TFTP runtime support.",
            permission=PermissionClass.READ_LOCAL,
            input_schema=_object_schema({}),
            tags=("file-transfer", "runtime", "read"),
        ),
        _handle_tftp_runtime,
    ),
    (
        _descriptor(
            name="tftp_upload",
            display_name="TFTP Upload",
            description="Upload one local file to a TFTP server.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "host": {"type": "string"},
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "default": 69,
                    },
                    "local_path": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "default": 5,
                    },
                    "retries": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "default": 3,
                    },
                },
                required=("host", "local_path", "remote_path"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Sends one local file to a remote TFTP server.",
            tags=("file-transfer", "remote", "tftp", "upload", "mutation"),
        ),
        _handle_tftp_upload,
    ),
    (
        _descriptor(
            name="tftp_download",
            display_name="TFTP Download",
            description="Download one remote file from a TFTP server.",
            permission=PermissionClass.CONNECT_REMOTE,
            input_schema=_object_schema(
                {
                    "host": {"type": "string"},
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "default": 69,
                    },
                    "remote_path": {"type": "string"},
                    "local_folder": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                        "default": 5,
                    },
                    "retries": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "default": 3,
                    },
                },
                required=("host", "remote_path", "local_folder"),
            ),
            risk_level="high",
            approval_required=True,
            impact="Reads a remote TFTP file and writes it into a local folder.",
            tags=("file-transfer", "remote", "tftp", "download", "mutation"),
        ),
        _handle_tftp_download,
    ),
    (
        _descriptor(
            name="update_check",
            display_name="Update Check",
            description="Check GitHub Releases for a newer NetOps Suite installer.",
            permission=PermissionClass.PROBE_NETWORK,
            input_schema=_object_schema(
                {
                    "current_version": {"type": "string"},
                    "repo": {"type": "string"},
                    "asset_pattern": {"type": "string"},
                }
            ),
            timeout_seconds=45,
            tags=("update", "read", "network"),
        ),
        _handle_update_check,
    ),
)

NETOPS_TOOL_DESCRIPTORS = tuple(
    descriptor for descriptor, _handler in NETOPS_TOOL_SPECS
)
