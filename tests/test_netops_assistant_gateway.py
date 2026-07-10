from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from app.assistant import (
    AuditLogger,
    PermissionClass,
    PolicyContext,
    PolicyEvaluator,
    ToolCallRequest,
    ToolDescriptor,
    ToolRegistry,
    build_netops_tool_registry,
    tool_call_from_netops_action,
)
from app.assistant.netops_tools import run_netops_tool


EXPECTED_PERMISSION_VALUES = {
    "READ_LOCAL": "read_local",
    "PROBE_NETWORK": "probe_network",
    "WRITE_LOCAL": "write_local",
    "WRITE_SYSTEM": "write_system",
    "CONNECT_REMOTE": "connect_remote",
}


def test_permission_class_exposes_expected_wire_values():
    assert {member.name for member in PermissionClass}.issuperset(EXPECTED_PERMISSION_VALUES)

    for member_name, wire_value in EXPECTED_PERMISSION_VALUES.items():
        assert _wire_value(getattr(PermissionClass, member_name)) == wire_value


def test_tool_registry_returns_descriptors_by_name():
    read_descriptor = _descriptor("netops.adapters.list", PermissionClass.READ_LOCAL)
    probe_descriptor = _descriptor("netops.ping", PermissionClass.PROBE_NETWORK)
    registry = _registry_with(read_descriptor, probe_descriptor)

    assert _descriptor_name(_lookup(registry, "netops.adapters.list")) == "netops.adapters.list"
    assert _wire_value(_descriptor_permission(_lookup(registry, "netops.adapters.list"))) == "read_local"
    assert _descriptor_name(_lookup(registry, "netops.ping")) == "netops.ping"
    assert _wire_value(_descriptor_permission(_lookup(registry, "netops.ping"))) == "probe_network"

    with pytest.raises((KeyError, LookupError)):
        _lookup(registry, "netops.missing")


@pytest.mark.parametrize(
    ("permission", "tool_name"),
    [
        (PermissionClass.READ_LOCAL, "netops.adapters.list"),
        (PermissionClass.PROBE_NETWORK, "netops.ping"),
    ],
)
def test_policy_auto_allows_read_local_and_probe_network_without_approval(permission, tool_name):
    registry = _registry_with(_descriptor(tool_name, permission))

    decision = _evaluate(registry, _request(tool_name, target="127.0.0.1"), is_admin=False)

    _assert_decision(decision, allowed=True, requires_approval=False, blocked=False)


@pytest.mark.parametrize(
    ("permission", "tool_name"),
    [
        (PermissionClass.WRITE_LOCAL, "netops.profile.save"),
        (PermissionClass.WRITE_SYSTEM, "netops.adapter.set_dns"),
        (PermissionClass.CONNECT_REMOTE, "netops.ssh.connect"),
    ],
)
def test_policy_requires_approval_for_write_and_remote_permissions(permission, tool_name):
    registry = _registry_with(_descriptor(tool_name, permission, admin_required=False))

    decision = _evaluate(registry, _request(tool_name, target="192.0.2.10"), is_admin=True)

    _assert_decision(decision, allowed=False, requires_approval=True, blocked=False)


def test_policy_blocks_admin_required_tool_when_context_is_not_admin():
    tool_name = "netops.adapter.set_static_ip"
    registry = _registry_with(_descriptor(tool_name, PermissionClass.WRITE_SYSTEM, admin_required=True))

    decision = _evaluate(
        registry,
        _request(tool_name, interface="Ethernet", ip_address="192.168.10.20"),
        is_admin=False,
        approval_granted=True,
    )

    _assert_decision(decision, allowed=False, requires_approval=False, blocked=True)
    assert "admin" in _decision_reason(decision).lower()


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_reason_fragment"),
    [
        ("shell", {"command": "ipconfig /all"}, "shell"),
        ("powershell", {"command": "Get-NetIPConfiguration"}, "powershell"),
        ("ssh", {"command": "show running-config", "host": "192.0.2.1"}, "ssh"),
    ],
)
def test_policy_blocks_raw_shell_powershell_and_ssh_tools(tool_name, arguments, expected_reason_fragment):
    registry = _registry_with(_descriptor(tool_name, PermissionClass.READ_LOCAL))

    decision = _evaluate(
        registry,
        _request(tool_name, **arguments),
        is_admin=True,
        approval_granted=True,
    )

    _assert_decision(decision, allowed=False, requires_approval=False, blocked=True)
    assert expected_reason_fragment in _decision_reason(decision).lower()


def test_audit_logger_redacts_sensitive_values_but_keeps_operational_context():
    payload = {
        "tool_name": "netops.ssh.connect",
        "arguments": {
            "host": "192.0.2.40",
            "username": "netadmin",
            "password": "DoNotLogMe!",
            "api_token": "tok_live_sensitive",
            "authorization": "Bearer bearer-sensitive",
            "command": "sshpass -p DoNotLogMe! ssh netadmin@192.0.2.40",
            "nested": {
                "private_key": "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----",
            },
        },
        "decision": "approval_required",
    }

    redacted = _redact_payload(payload)
    audit_text = json.dumps(redacted, default=str, sort_keys=True)

    assert "netops.ssh.connect" in audit_text
    assert "192.0.2.40" in audit_text
    assert "approval_required" in audit_text
    assert "DoNotLogMe!" not in audit_text
    assert "tok_live_sensitive" not in audit_text
    assert "bearer-sensitive" not in audit_text
    assert "BEGIN PRIVATE KEY" not in audit_text
    assert "redact" in audit_text.lower()


def test_netops_tool_registry_exposes_current_suite_capability_groups():
    registry = build_netops_tool_registry()

    expected_tools = {
        "ping",
        "ping_batch",
        "external_ping",
        "tcp_check",
        "tcp_batch",
        "subnet_calculate",
        "dns_lookup",
        "dns_flush_cache",
        "public_ip",
        "tracert",
        "pathping",
        "ipconfig",
        "route_print",
        "arp_table",
        "interface_snapshot",
        "app_paths",
        "artifacts_list",
        "set_dns",
        "set_dhcp",
        "set_static_ip",
        "wifi_status",
        "wifi_scan_nearby",
        "oui_lookup",
        "oui_cache_summary",
        "oui_cache_refresh",
        "inspector_profiles_list",
        "config_builder_profiles_list",
        "ip_profiles",
        "ftp_profiles",
        "scp_profiles",
        "arp_scan",
        "iperf_status",
        "iperf_client_test",
        "ftp_connect",
        "ftp_upload",
        "ftp_download",
        "scp_upload",
        "scp_download",
        "tftp_upload",
        "tftp_download",
        "update_check",
    }
    assert expected_tools.issubset(set(registry.names()))
    assert _descriptor_name(_lookup(registry, "net.interface.set_dns")) == "set_dns"
    assert _descriptor_name(_lookup(registry, "net.ping.batch")) == "ping_batch"
    assert _descriptor_name(_lookup(registry, "net.tcp.batch")) == "tcp_batch"
    assert _descriptor_name(_lookup(registry, "net.subnet.calculate")) == "subnet_calculate"
    assert _descriptor_name(_lookup(registry, "net.dns.flush_cache")) == "dns_flush_cache"
    assert _descriptor_name(_lookup(registry, "wifi.scan_nearby")) == "wifi_scan_nearby"
    assert _descriptor_name(_lookup(registry, "wifi.scan")) == "wifi_scan_nearby"
    assert _descriptor_name(_lookup(registry, "net.wifi.scan")) == "wifi_scan_nearby"
    assert _descriptor_name(_lookup(registry, "netops.wifi.scan")) == "wifi_scan_nearby"
    assert _descriptor_name(_lookup(registry, "wireless_scan")) == "wifi_scan_nearby"
    assert _descriptor_name(_lookup(registry, "file_transfer.scp.upload")) == "scp_upload"
    assert _descriptor_name(_lookup(registry, "app.paths")) == "app_paths"
    assert _descriptor_name(_lookup(registry, "artifacts.list")) == "artifacts_list"
    assert _descriptor_name(_lookup(registry, "oui.cache_summary")) == "oui_cache_summary"
    assert _descriptor_name(_lookup(registry, "oui.cache.refresh")) == "oui_cache_refresh"
    assert _descriptor_name(_lookup(registry, "inspector.profiles.list")) == "inspector_profiles_list"
    assert _descriptor_name(_lookup(registry, "config_builder.profiles.list")) == "config_builder_profiles_list"


@pytest.mark.parametrize(
    "tool_name",
    [
        "app.paths",
        "artifacts.list",
        "oui.cache_summary",
        "inspector.profiles.list",
        "config_builder.profiles.list",
    ],
)
def test_new_read_local_netops_tools_do_not_require_approval(tool_name):
    registry = build_netops_tool_registry()

    descriptor = _lookup(registry, tool_name)
    decision = _evaluate(registry, _request(tool_name), is_admin=False)

    assert _wire_value(descriptor.permission_class) == "read_local"
    assert descriptor.risk_level == "low"
    assert descriptor.approval_required is None
    _assert_decision(decision, allowed=True, requires_approval=False, blocked=False)


@pytest.mark.parametrize(
    ("tool_name", "permission"),
    [
        ("net.ping.batch", "probe_network"),
        ("net.tcp.batch", "probe_network"),
        ("net.subnet.calculate", "read_local"),
    ],
)
def test_p2_read_and_probe_netops_tools_do_not_require_approval(tool_name, permission):
    registry = build_netops_tool_registry()

    descriptor = _lookup(registry, tool_name)
    decision = _evaluate(registry, _request(tool_name), is_admin=False)

    assert _wire_value(descriptor.permission_class) == permission
    assert descriptor.risk_level == "low"
    assert descriptor.approval_required is None
    _assert_decision(decision, allowed=True, requires_approval=False, blocked=False)


def test_dns_flush_cache_blocks_without_admin_and_requires_approval_with_admin():
    registry = build_netops_tool_registry()

    blocked = _evaluate(registry, _request("net.dns.flush_cache"), is_admin=False, approval_granted=True)
    needs_approval = _evaluate(registry, _request("net.dns.flush_cache"), is_admin=True)

    _assert_decision(blocked, allowed=False, requires_approval=False, blocked=True)
    _assert_decision(needs_approval, allowed=False, requires_approval=True, blocked=False)


def test_descriptor_helper_propagates_risk_and_timeout_fields():
    registry = build_netops_tool_registry()

    descriptor = _lookup(registry, "net.interface.set_dns")

    assert descriptor.risk_level == "medium"
    assert descriptor.impact
    assert descriptor.reversibility
    assert descriptor.timeout_seconds == 30
    assert descriptor.metadata["risk_level"] == descriptor.risk_level
    assert descriptor.metadata["timeout_seconds"] == descriptor.timeout_seconds


def test_wifi_scan_nearby_descriptor_aliases_policy_and_fake_handler(monkeypatch):
    registry = build_netops_tool_registry()
    descriptor = _lookup(registry, "net.wifi.scan_nearby")

    assert descriptor.name == "wifi_scan_nearby"
    assert _wire_value(descriptor.permission_class) == "probe_network"
    assert descriptor.risk_level == "low"
    assert descriptor.approval_required is None
    assert "scan" in descriptor.impact.lower()
    decision = _evaluate(registry, _request("wifi.scan_nearby", duration_seconds=5, interval_seconds=2), is_admin=False)
    _assert_decision(decision, allowed=True, requires_approval=False, blocked=False)

    class FakeWirelessService:
        def __init__(self):
            self.calls: list[dict] = []

        def scan_nearby_access_points_window(self, **kwargs):
            self.calls.append(kwargs)
            access_point = SimpleNamespace(
                ssid="Lab",
                bssid="00:11:22:33:44:55",
                signal_percent=82,
                signal_text="82%",
                channel="6",
                band="2.4 GHz",
                authentication="WPA2-Personal",
                encryption="CCMP",
                sample_count=3,
                unstable=False,
            )
            channel = SimpleNamespace(
                channel="6",
                band="2.4 GHz",
                access_point_count=1,
                observation_count=3,
                average_signal_percent=82.0,
                average_channel_utilization_percent=None,
            )
            return SimpleNamespace(
                duration_seconds=20,
                interval_seconds=5,
                sample_count=3,
                actual_duration_seconds=10.4,
                cancelled=False,
                sample_limit_reached=False,
                observed_access_points=[access_point],
                channel_summaries=[channel],
                unstable_access_points=[],
                errors=[],
            )

    monkeypatch.setattr("app.assistant.netops_tools.time.sleep", lambda _seconds: None)
    wireless_service = FakeWirelessService()
    state = SimpleNamespace(wireless_service=wireless_service)

    result = run_netops_tool(state, "wireless.scan", {"duration_seconds": 20, "interval_seconds": 5})

    assert result.success is True
    assert wireless_service.calls == [{"duration_seconds": 20, "interval_seconds": 5, "include_oui": True}]
    assert "Nearby Wi-Fi scan completed: 1" in result.message
    assert getattr(result.payload["access_points"][0], "ssid") == "Lab"
    assert result.payload["duration_seconds"] == 20
    assert result.payload["interval_seconds"] == 5
    assert result.payload["sample_count"] == 3
    assert "scan_count" not in result.payload
    assert result.payload["actual_duration_seconds"] == pytest.approx(10.4)
    assert "Requested window: 20s / interval 5s" in result.details
    assert "Measured elapsed: 10.4s" in result.details
    assert result.payload["channel_summaries"][0].channel == "6"


def test_wifi_scan_internal_cancel_event_is_forwarded_but_not_publicly_exposed():
    registry = build_netops_tool_registry()
    descriptor = _lookup(registry, "wifi.scan_nearby")
    assert "_cancel_event" not in descriptor.input_schema["properties"]

    cancel_event = Event()

    class FakeWirelessService:
        def __init__(self):
            self.cancel_event = None

        def scan_nearby_access_points_window(self, **kwargs):
            self.cancel_event = kwargs.get("cancel_event")
            return SimpleNamespace(
                duration_seconds=20,
                interval_seconds=5,
                sample_count=0,
                actual_duration_seconds=0.0,
                cancelled=True,
                sample_limit_reached=False,
                observed_access_points=[],
                channel_summaries=[],
                unstable_access_points=[],
                errors=[],
            )

    wireless_service = FakeWirelessService()
    state = SimpleNamespace(wireless_service=wireless_service)

    result = run_netops_tool(
        state,
        "wireless.scan",
        {"duration_seconds": 20, "interval_seconds": 5, "_cancel_event": cancel_event},
    )

    assert wireless_service.cancel_event is cancel_event
    assert result.success is False
    assert result.payload["cancelled"] is True
    assert "cancelled" in result.message.lower()


def test_wifi_scan_fallback_loop_honors_internal_pre_cancel_event():
    cancel_event = Event()
    cancel_event.set()

    class LegacyWirelessService:
        def __init__(self):
            self.calls = 0

        def scan_nearby_access_points(self):
            self.calls += 1
            return []

    wireless_service = LegacyWirelessService()
    state = SimpleNamespace(wireless_service=wireless_service)

    result = run_netops_tool(
        state,
        "wireless.scan",
        {"duration_seconds": 20, "interval_seconds": 5, "_cancel_event": cancel_event},
    )

    assert wireless_service.calls == 0
    assert result.success is False
    assert result.payload["sample_count"] == 0
    assert result.payload["cancelled"] is True


def test_new_read_local_netops_tools_use_fake_state_and_are_read_only(tmp_path):
    paths = _fake_paths(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.logs_dir.mkdir(parents=True)
    paths.exports_dir.mkdir(parents=True)
    (paths.data_root / "inspector" / "runs").mkdir(parents=True)
    (paths.data_root / "config_builder").mkdir(parents=True)
    paths.app_log.write_text("log line\n", encoding="utf-8")
    paths.oui_cache.write_text('{"entries": []}\n', encoding="utf-8")
    export_path = paths.exports_dir / "ping_result.txt"
    export_path.write_text("ok\n", encoding="utf-8")
    inspector_path = paths.data_root / "inspector" / "runs" / "inspection_results.xlsx"
    inspector_path.write_text("placeholder\n", encoding="utf-8")
    hidden_path = paths.exports_dir / ".hidden.txt"
    hidden_path.write_text("hidden\n", encoding="utf-8")

    class FakeOuiService:
        def cache_summary(self):
            return "2 registries cached"

    class FakeInspectorService:
        def supported_profile_definitions(self):
            return [
                {
                    "display_name": "Cisco / IOS",
                    "key": "cisco|ios",
                    "command_count": 3,
                    "has_backup": True,
                    "source": "fake",
                }
            ]

    class FakeConfigBuilderService:
        profiles_dir = Path("fake_profiles")

        def profile_summaries(self):
            return [
                {
                    "id": "access-switch",
                    "vendor": "Cisco",
                    "model": "IOS",
                    "variables": ["hostname", "mgmt_ip"],
                    "blocks": ["base"],
                    "source": "fake.yaml",
                    "issue_count": 0,
                }
            ]

    state = SimpleNamespace(
        paths=paths,
        oui_service=FakeOuiService(),
        inspector_service=FakeInspectorService(),
        config_builder_service=FakeConfigBuilderService(),
    )

    app_paths = run_netops_tool(state, "app.paths")
    artifacts = run_netops_tool(state, "artifacts.list", {"limit": 10})
    oui_summary = run_netops_tool(state, "oui.cache_summary")
    inspector_profiles = run_netops_tool(state, "inspector.profiles.list")
    config_profiles = run_netops_tool(state, "config_builder.profiles.list")

    assert app_paths.success is True
    assert {item["name"] for item in app_paths.payload} >= {"root", "data_root", "exports_dir", "oui_cache"}
    assert artifacts.success is True
    artifact_names = [item["name"] for item in artifacts.payload["artifacts"]]
    assert "ping_result.txt" in artifact_names
    assert "inspection_results.xlsx" in artifact_names
    assert ".hidden.txt" not in artifact_names
    assert len(artifact_names) == len(set(artifact_names))
    assert oui_summary.success is True
    assert oui_summary.payload["summary"] == "2 registries cached"
    assert oui_summary.payload["cache"]["exists"] is True
    assert inspector_profiles.success is True
    assert inspector_profiles.payload["profiles"][0]["key"] == "cisco|ios"
    assert config_profiles.success is True
    assert config_profiles.payload["profiles"][0]["id"] == "access-switch"


def test_p2_netops_tools_use_existing_services_through_fake_state():
    class FakePingService:
        def __init__(self):
            self.calls = []

        def run_multi_ping(self, raw_targets, count, timeout_ms, max_workers, continuous=False):
            self.calls.append((raw_targets, count, timeout_ms, max_workers, continuous))
            return [
                SimpleNamespace(
                    target="8.8.8.8",
                    success=True,
                    status="ok",
                    sent=2,
                    received=2,
                    packet_loss=0,
                    min_rtt=1.0,
                    avg_rtt=1.5,
                    max_rtt=2.0,
                    error="",
                ),
                SimpleNamespace(
                    target="1.1.1.1",
                    success=False,
                    status="timeout",
                    sent=2,
                    received=0,
                    packet_loss=100,
                    error="timeout",
                ),
            ]

    class FakeTcpCheckService:
        def __init__(self):
            self.calls = []

        def run_multi_check(self, raw_targets, raw_ports, count, timeout_ms, max_workers, continuous=False):
            self.calls.append((raw_targets, raw_ports, count, timeout_ms, max_workers, continuous))
            return [
                SimpleNamespace(
                    target="example.com",
                    port=443,
                    status="open",
                    sent=2,
                    successful=2,
                    packet_loss=0,
                    response_ms=12.3,
                    error="",
                )
            ]

    class FakeDnsService:
        def flush_dns_cache(self):
            return SimpleNamespace(success=True, message="DNS flushed", details="")

    class FakeOuiService:
        def refresh_cache(self):
            return SimpleNamespace(success=True, message="OUI refreshed", details="42 records", payload={"count": 42})

    ping_service = FakePingService()
    tcp_check_service = FakeTcpCheckService()
    state = SimpleNamespace(
        ping_service=ping_service,
        tcp_check_service=tcp_check_service,
        dns_service=FakeDnsService(),
        oui_service=FakeOuiService(),
    )

    ping = run_netops_tool(state, "net.ping.batch", {"targets": ["8.8.8.8", "1.1.1.1"], "count": 2})
    tcp = run_netops_tool(state, "net.tcp.batch", {"targets": ["example.com"], "ports": [443], "count": 2})
    subnet = run_netops_tool(
        state,
        "net.subnet.calculate",
        {"cidr": "192.168.10.0/24", "include_hosts": True, "max_hosts": 3},
    )
    dns = run_netops_tool(state, "net.dns.flush_cache")
    oui = run_netops_tool(state, "oui.cache.refresh")

    assert ping.success is True
    assert ping.status == "partial"
    assert ping_service.calls == [("8.8.8.8\n1.1.1.1", 2, 4000, 2, False)]
    assert tcp.success is True
    assert tcp_check_service.calls == [("example.com", "443", 2, 4000, 1, False)]
    assert subnet.success is True
    assert subnet.payload["network"] == "192.168.10.0/24"
    assert subnet.payload["hosts"] == ["192.168.10.1", "192.168.10.2", "192.168.10.3"]
    assert subnet.payload["hosts_truncated"] is True
    assert dns.success is True
    assert dns.message == "DNS flushed"
    assert oui.success is True
    assert oui.payload == {"count": 42}


def test_wireless_scan_action_maps_to_tool_call_arguments():
    action = SimpleNamespace(kind="wireless_scan", duration_seconds=45, interval_seconds=15)

    call = tool_call_from_netops_action(action, user_intent="nearby wifi")

    assert call.tool_name == "wifi.scan_nearby"
    assert call.arguments == {"duration_seconds": 45, "interval_seconds": 15}


def test_p2_netops_actions_map_to_tool_call_arguments():
    subnet = tool_call_from_netops_action(SimpleNamespace(kind="subnet_calculate", target="192.168.1.0/24"))
    dns_flush = tool_call_from_netops_action(SimpleNamespace(kind="dns_flush_cache"))
    oui_refresh = tool_call_from_netops_action(SimpleNamespace(kind="oui_cache_refresh"))

    assert subnet.tool_name == "net.subnet.calculate"
    assert subnet.arguments == {"cidr": "192.168.1.0/24"}
    assert dns_flush.tool_name == "net.dns.flush_cache"
    assert dns_flush.arguments == {}
    assert oui_refresh.tool_name == "oui.cache.refresh"
    assert oui_refresh.arguments == {}


@pytest.mark.parametrize(
    "tool_name",
    ["set_dns", "dns_flush_cache", "oui_cache_refresh", "ftp_upload", "scp_download", "tftp_upload", "public_iperf_refresh"],
)
def test_risky_netops_suite_tools_require_policy_approval(tool_name):
    registry = build_netops_tool_registry()

    decision = _evaluate(registry, _request(tool_name), is_admin=True)

    _assert_decision(decision, allowed=False, requires_approval=True, blocked=False)


def _descriptor(name: str, permission, *, admin_required: bool = False):
    try:
        return ToolDescriptor(
            name=name,
            description=f"Test descriptor for {name}",
            permission=permission,
            admin_required=admin_required,
        )
    except TypeError:
        return ToolDescriptor(
            name=name,
            description=f"Test descriptor for {name}",
            permission_class=permission,
            admin_required=admin_required,
        )


def _registry_with(*descriptors):
    for args in ((list(descriptors),), descriptors):
        try:
            return ToolRegistry(*args)
        except TypeError:
            pass

    registry = ToolRegistry()
    for descriptor in descriptors:
        for method_name in ("register", "add", "add_descriptor"):
            method = getattr(registry, method_name, None)
            if method is not None:
                method(descriptor)
                break
        else:
            raise AssertionError("ToolRegistry must accept descriptors or expose register/add")
    return registry


def _lookup(registry, name: str):
    for method_name in ("lookup", "get", "descriptor_for", "resolve"):
        method = getattr(registry, method_name, None)
        if method is not None:
            descriptor = method(name)
            if descriptor is None:
                raise KeyError(name)
            return descriptor
    raise AssertionError("ToolRegistry must expose lookup/get/descriptor_for/resolve")


def _request(tool_name: str, **arguments):
    try:
        return ToolCallRequest(tool_name=tool_name, arguments=arguments)
    except TypeError:
        return ToolCallRequest(name=tool_name, args=arguments)


def _fake_paths(root: Path):
    config_dir = root / "config"
    logs_dir = root / "logs"
    return SimpleNamespace(
        root=root,
        data_root=root,
        config_dir=config_dir,
        logs_dir=logs_dir,
        exports_dir=logs_dir / "exports",
        app_config=config_dir / "app_config.json",
        ip_profiles=config_dir / "ip_profiles.json",
        ftp_profiles=config_dir / "ftp_profiles.json",
        ftp_runtime=config_dir / "ftp_runtime.json",
        scp_profiles=config_dir / "scp_profiles.json",
        scp_runtime=config_dir / "scp_runtime.json",
        tftp_runtime=config_dir / "tftp_runtime.json",
        vendor_presets=config_dir / "vendor_presets.json",
        public_iperf_cache=config_dir / "public_iperf_servers_cache.json",
        oui_cache=config_dir / "oui_cache.json",
        ftp_keys_dir=config_dir / "ftp_keys",
        app_log=logs_dir / "app.log",
    )


def _context(*, is_admin: bool, approval_granted: bool = False):
    for approval_name in ("approval_granted", "approved", "user_approved"):
        try:
            return PolicyContext(is_admin=is_admin, **{approval_name: approval_granted})
        except TypeError:
            pass
    return PolicyContext(is_admin=is_admin)


def _evaluate(registry, request, *, is_admin: bool, approval_granted: bool = False):
    evaluator = PolicyEvaluator(registry)
    return evaluator.evaluate(request, _context(is_admin=is_admin, approval_granted=approval_granted))


def _redact_payload(payload):
    logger = AuditLogger()
    for owner in (logger, AuditLogger):
        for method_name in ("redact_payload", "redact", "sanitize_payload", "sanitize"):
            method = getattr(owner, method_name, None)
            if method is None:
                continue
            try:
                return method(payload)
            except TypeError:
                continue
    raise AssertionError("AuditLogger must expose a payload redaction/sanitization method")


def _wire_value(value) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _descriptor_name(descriptor) -> str:
    return str(getattr(descriptor, "name", getattr(descriptor, "tool_name", "")))


def _descriptor_permission(descriptor):
    return getattr(descriptor, "permission", getattr(descriptor, "permission_class", None))


def _assert_decision(decision, *, allowed: bool, requires_approval: bool, blocked: bool):
    assert _decision_flag(decision, "allowed") is allowed
    assert _decision_flag(decision, "requires_approval") is requires_approval
    assert _decision_flag(decision, "blocked") is blocked


def _decision_flag(decision, name: str) -> bool:
    if hasattr(decision, name):
        return bool(getattr(decision, name))
    if isinstance(decision, dict) and name in decision:
        return bool(decision[name])

    status = _wire_value(getattr(decision, "status", decision)).lower()
    if name == "allowed":
        return status in {"allow", "allowed", "auto_allow", "auto_allowed"}
    if name == "requires_approval":
        return status in {"approval_required", "requires_approval", "require_approval"}
    if name == "blocked":
        return status in {"block", "blocked", "deny", "denied"}
    raise AssertionError(f"Unknown decision flag: {name}")


def _decision_reason(decision) -> str:
    if isinstance(decision, dict):
        return str(decision.get("reason", ""))
    return str(getattr(decision, "reason", getattr(decision, "message", "")))
