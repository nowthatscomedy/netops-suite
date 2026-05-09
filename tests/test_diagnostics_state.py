from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt

from app.models.ftp_models import FtpProfile
from app.models.network_models import NetworkAdapterInfo, OuiRecord, PublicIperfServer
from app.models.result_models import OperationResult, PingResult, TcpCheckResult
from app.models.scp_models import ScpProfile
from app.ui.tabs.diagnostics_tab import DiagnosticsTab


class SyncThreadPool:
    def start(self, worker) -> None:
        worker.run()


class FakeNetworkInterfaceService:
    def list_adapters(self):
        return [
            NetworkAdapterInfo(
                name="Ethernet",
                interface_description="Intel Ethernet",
                mac_address="00-11-22-33-44-55",
                status="Up",
                ipv4="192.168.0.10",
                prefix_length=24,
            )
        ]

    def format_adapter_snapshot(self, adapters) -> str:
        return f"{len(adapters)} adapters"


class FakeArpScanService:
    def list_candidate_subnets(self, adapters):
        return [("Ethernet - 192.168.0.0/24", "192.168.0.0/24")]

    def run_scan(self, *args, **kwargs):
        return OperationResult(True, "scan complete", payload=[])


class FakeOuiService:
    def cache_summary(self) -> str:
        return "로컬 캐시 1건"

    def split_label_and_mac(self, value: str):
        if "," in value:
            name, mac = [part.strip() for part in value.split(",", 1)]
            return name, mac
        return value, value

    def normalize_mac(self, mac_address: str) -> str:
        return "".join(ch for ch in mac_address.upper() if ch in "0123456789ABCDEF")

    def lookup(self, mac_address: str):
        normalized = self.normalize_mac(mac_address)
        if len(normalized) >= 6:
            return OuiRecord(prefix=normalized[:6], prefix_bits=24, organization="Vendor", registry="MA-L")
        return None

    def refresh_cache(self, *args, **kwargs):
        return OperationResult(True, "cache refreshed")


class FakeDnsService:
    def lookup(self, *args, **kwargs):
        return OperationResult(True, "dns ok", "details")

    def flush_dns_cache(self):
        return OperationResult(True, "flush ok")


class FakeTraceService:
    def run_tracert(self, *args, **kwargs):
        return OperationResult(True, "trace ok")

    def run_pathping(self, *args, **kwargs):
        return OperationResult(True, "pathping ok")

    def run_ipconfig_all(self):
        return OperationResult(True, "ipconfig", "details")

    def run_route_print(self):
        return OperationResult(True, "route", "details")

    def run_arp_table(self):
        return OperationResult(True, "arp", "details")


class FakeIperfService:
    def executable_details(self):
        return (None, "")

    def managed_install_state(self):
        return {
            "available": False,
            "installed": False,
            "update_available": False,
            "button_enabled": False,
            "action_label": "winget 없음",
            "package_id": "fake.iperf3",
            "package_url": "https://example.com",
        }

    def executable_version(self, executable_path=None):
        return None

    def managed_package_page(self):
        return "https://example.com"

    def install_or_update_managed(self, *args, **kwargs):
        return OperationResult(True, "install ok")

    def run_test(self, *args, **kwargs):
        return OperationResult(True, "iperf ok")


class FakePublicIperfService:
    def __init__(self):
        self.server = PublicIperfServer(
            name="Seoul",
            host="iperf.example.com",
            port_spec="5201",
            default_port=5201,
            region="asia",
            site="Seoul",
            country_code="KR",
        )

    def load_cached_servers(self):
        return OperationResult(
            True,
            "cached",
            payload={
                "servers": [self.server],
                "fetched_at": "2026-04-18T00:00:00Z",
                "from_cache": True,
                "stale": False,
            },
        )

    def fetch_public_servers(self, force_refresh: bool = False):
        return OperationResult(
            True,
            "fetched",
            payload={
                "servers": [self.server],
                "fetched_at": "2026-04-18T00:00:00Z",
                "from_cache": False,
                "stale": False,
            },
        )


class FakeFtpClientService:
    def runtime_support_status(self, protocol: str):
        return OperationResult(True, f"{protocol} client ready")


class FakeFtpServerService:
    def runtime_support_status(self, protocol: str):
        return OperationResult(True, f"{protocol} server ready")


class FakeScpClientService:
    def runtime_support_status(self):
        return OperationResult(True, "scp client ready")


class FakeScpServerService:
    def runtime_support_status(self):
        return OperationResult(True, "scp server ready")


class FakeTftpService:
    def runtime_support_status(self):
        return OperationResult(True, "tftp ready")


def build_fake_state(tmp_path):
    state = SimpleNamespace(
        thread_pool=SyncThreadPool(),
        app_config={
            "default_ping_count": 4,
            "default_ping_timeout_ms": 4000,
            "default_ping_workers": 8,
            "default_tcp_timeout_ms": 1000,
            "default_tcp_workers": 32,
        },
        paths=SimpleNamespace(exports_dir=tmp_path, root=tmp_path),
        network_interface_service=FakeNetworkInterfaceService(),
        arp_scan_service=FakeArpScanService(),
        oui_service=FakeOuiService(),
        dns_service=FakeDnsService(),
        trace_service=FakeTraceService(),
        iperf_service=FakeIperfService(),
        public_iperf_service=FakePublicIperfService(),
        ftp_client_service=FakeFtpClientService(),
        ftp_server_service=FakeFtpServerService(),
        scp_client_service=FakeScpClientService(),
        scp_server_service=FakeScpServerService(),
        tftp_service=FakeTftpService(),
        ftp_profiles=[
            FtpProfile(
                name="Lab FTP",
                protocol="ftp",
                host="192.168.0.20",
                port=21,
                username="tester",
                remote_path="/upload",
                passive_mode=True,
                timeout_seconds=15,
            )
        ],
        ftp_runtime={
            "client": {
                "protocol": "ftps",
                "host": "files.example.com",
                "port": "2121",
                "username": "field",
                "passive_mode": True,
                "timeout_seconds": "20",
                "local_folder": str(tmp_path),
                "remote_path": "/backup",
                "selected_profile": "Lab FTP",
            },
            "server": {
                "protocol": "sftp",
                "bind_host": "0.0.0.0",
                "port": "2222",
                "root_folder": str(tmp_path),
                "username": "netops",
                "read_only": True,
                "anonymous_readonly": False,
            },
        },
        scp_profiles=[
            ScpProfile(
                name="Lab SCP",
                host="192.168.0.30",
                port=22,
                username="netops",
                remote_path="/backup",
                timeout_seconds=15,
            )
        ],
        scp_runtime={
            "client": {
                "host": "scp.example.com",
                "port": "2222",
                "username": "field",
                "timeout_seconds": "25",
                "remote_path": "/drop",
                "remote_sources": "/var/log/messages",
                "local_folder": str(tmp_path),
                "selected_profile": "Lab SCP",
            },
            "server": {
                "bind_host": "0.0.0.0",
                "port": "2223",
                "root_folder": str(tmp_path),
                "username": "share",
                "read_only": True,
            },
        },
        tftp_runtime={
            "client": {
                "host": "tftp.example.com",
                "port": "1069",
                "remote_path": "config/startup.cfg",
                "local_folder": str(tmp_path),
                "local_upload_path": str(tmp_path / "upload.cfg"),
                "timeout_seconds": "8",
                "retries": "4",
            },
            "server": {
                "bind_host": "0.0.0.0",
                "port": "1069",
                "root_folder": str(tmp_path),
                "read_only": True,
            },
        },
        ping_service=SimpleNamespace(),
        tcp_check_service=SimpleNamespace(),
    )
    state.saved_ftp_runtime = None
    state.saved_ftp_profiles = None
    state.saved_scp_runtime = None
    state.saved_scp_profiles = None
    state.saved_tftp_runtime = None

    def save_ftp_runtime(runtime):
        state.saved_ftp_runtime = runtime

    def save_ftp_profiles(profiles):
        state.saved_ftp_profiles = profiles
        state.ftp_profiles = profiles

    def save_scp_runtime(runtime):
        state.saved_scp_runtime = runtime

    def save_scp_profiles(profiles):
        state.saved_scp_profiles = profiles
        state.scp_profiles = profiles

    def save_tftp_runtime(runtime):
        state.saved_tftp_runtime = runtime

    state.save_ftp_runtime = save_ftp_runtime
    state.save_ftp_profiles = save_ftp_profiles
    state.save_scp_runtime = save_scp_runtime
    state.save_scp_profiles = save_scp_profiles
    state.save_tftp_runtime = save_tftp_runtime
    return state


def test_diagnostics_state_save_and_restore_shape(qapp, tmp_path):
    state = build_fake_state(tmp_path)
    tab = DiagnosticsTab(state)

    tab.subnet_calc_ip_edit.setText("192.168.0.10")
    tab.subnet_calc_prefix_edit.setText("24")
    tab.arp_subnet_edit.setText("192.168.0.0/24")
    tab.arp_timeout_edit.setText("900")
    tab.arp_workers_edit.setText("10")
    tab.oui_mac_edit.setPlainText("AP,00:11:22:33:44:55")
    tab.ping_targets_edit.setPlainText("GW,192.168.0.1")
    tab.tcp_targets_edit.setPlainText("GW,192.168.0.1")
    tab.tcp_ports_edit.setText("443")
    tab.dns_query_edit.setText("example.com")
    tab.trace_target_edit.setText("8.8.8.8")
    tab.iperf_use_public_server_check.setChecked(True)
    tab.iperf_server_edit.setText("iperf.example.com")

    tab.file_transfer_role_combo.setCurrentIndex(1)
    tab.file_transfer_mode_combo.setCurrentIndex(1)

    saved = tab.save_ui_state()

    assert set(saved) == {"current_tab", "tools", "ping", "tcp", "dns", "trace", "ftp", "iperf"}
    assert saved["tools"]["version"] == 2
    assert saved["tools"]["subnet_ip"] == "192.168.0.10"
    assert saved["tools"]["oui_targets"] == "AP,00:11:22:33:44:55"
    assert saved["ftp"]["role_tab"] == 1
    assert saved["ftp"]["client_protocol_tab"] == 0
    assert saved["ftp"]["server_protocol_tab"] == 1
    assert saved["ftp"]["current_subtab"] == 4
    assert saved["iperf"]["public_server_key"] == "iperf.example.com|5201"
    assert state.saved_ftp_runtime["client"]["host"] == "files.example.com"
    assert state.saved_scp_runtime["client"]["host"] == "scp.example.com"
    assert state.saved_tftp_runtime["client"]["host"] == "tftp.example.com"

    legacy_state = {
        "current_tab": 7,
        "tools": {
            "version": 1,
            "current_subtab": 2,
            "subnet_ip": "10.0.0.5",
            "subnet_prefix": "24",
            "arp_subnet": "10.0.0.0/24",
            "arp_timeout_ms": "700",
            "arp_workers": "5",
            "oui_mac": "Legacy,AA:BB:CC:DD:EE:FF",
        },
        "ping": {"targets": "A,1.1.1.1", "count": "5", "timeout_ms": "1000", "workers": "2", "continuous": True},
        "tcp": {
            "targets": "B,2.2.2.2",
            "ports": "80",
            "count": "3",
            "timeout_ms": "900",
            "workers": "4",
            "continuous": False,
        },
        "dns": {"query": "openai.com", "record_type": "AAAA", "server": "8.8.8.8"},
        "trace": {"target": "9.9.9.9", "no_resolve": True},
        "ftp": {},
        "iperf": {
            "mode": "client",
            "use_public_server": True,
            "public_region": "asia",
            "public_server_key": "iperf.example.com|5201",
            "server": "iperf.example.com",
            "port": "5201",
            "streams": "2",
            "duration": "15",
            "reverse": True,
            "udp": False,
            "ipv6": False,
        },
        "scp": {"current_subtab": 1},
    }

    tab.restore_ui_state(legacy_state)

    assert tab.tab_widget.currentIndex() == 5
    assert tab.tools_inner_tab.currentIndex() == 3
    assert tab.subnet_calc_ip_edit.text() == "10.0.0.5"
    assert tab.arp_subnet_edit.text() == "10.0.0.0/24"
    assert tab.oui_mac_edit.toPlainText() == "Legacy,AA:BB:CC:DD:EE:FF"
    assert tab.ping_targets_edit.toPlainText() == "A,1.1.1.1"
    assert tab.tcp_ports_edit.text() == "80"
    assert tab.dns_query_edit.text() == "openai.com"
    assert tab.trace_no_resolve_check.isChecked() is True
    assert tab.file_transfer_role_combo.currentIndex() == 1
    assert tab.file_transfer_mode_combo.currentIndex() == 1
    assert tab.ftp_client_host_edit.text() == "files.example.com"
    assert tab.ftp_server_port_edit.text() == "2222"
    assert tab.scp_client_host_edit.text() == "scp.example.com"
    assert tab.scp_server_port_edit.text() == "2223"
    assert tab.iperf_public_region_combo.currentData() == "asia"
    assert tab.iperf_public_server_combo.currentData() == "iperf.example.com|5201"

    restored_saved = tab.save_ui_state()
    assert restored_saved["tools"]["version"] == 2
    assert restored_saved["tools"]["oui_targets"] == "Legacy,AA:BB:CC:DD:EE:FF"


def test_ping_table_sorts_numeric_columns_and_updates_sorted_rows(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))

    tab._handle_ping_progress(
        {
            "result": PingResult(
                name="slow",
                target="192.0.2.100",
                success=True,
                status="정상",
                packet_loss=0,
                sent=10,
                received=10,
                min_rtt=100,
                avg_rtt=100,
                max_rtt=100,
                last_seen="10:00:00",
            ),
            "line": "slow reply",
        }
    )
    tab._handle_ping_progress(
        {
            "result": PingResult(
                name="fast",
                target="192.0.2.9",
                success=True,
                status="정상",
                packet_loss=0,
                sent=2,
                received=2,
                min_rtt=9,
                avg_rtt=9,
                max_rtt=9,
                last_seen="10:00:01",
            ),
            "line": "fast reply",
        }
    )

    tab.ping_table.sortItems(8, Qt.SortOrder.AscendingOrder)
    assert tab._cell(tab.ping_table, 0, 8) == "9.0"

    tab._handle_ping_progress(
        {
            "result": PingResult(
                name="slow",
                target="192.0.2.100",
                success=True,
                status="정상",
                packet_loss=0,
                sent=11,
                received=11,
                min_rtt=5,
                avg_rtt=5,
                max_rtt=5,
                last_seen="10:00:02",
            ),
            "line": "slow faster",
        }
    )

    assert tab._cell(tab.ping_table, 0, 0) == "slow"
    assert tab._cell(tab.ping_table, 0, 8) == "5.0"
    assert tab.ping_row_map[("slow", "192.0.2.100")] == 0


def test_tcp_table_sorts_port_and_loss_as_numbers(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))

    tab._handle_tcp_progress(
        {
            "result": TcpCheckResult(
                name="high",
                target="198.51.100.10",
                port=100,
                status="열림",
                sent=4,
                successful=4,
                failed=0,
                packet_loss=0,
                min_response_ms=100,
                response_ms=100,
                max_response_ms=100,
                last_seen="10:00:00",
            ),
            "line": "high open",
        }
    )
    tab._handle_tcp_progress(
        {
            "result": TcpCheckResult(
                name="low",
                target="198.51.100.10",
                port=9,
                status="응답 없음",
                sent=4,
                successful=0,
                failed=4,
                packet_loss=100,
                min_response_ms=None,
                response_ms=None,
                max_response_ms=None,
                last_seen="10:00:01",
            ),
            "line": "low timeout",
        }
    )

    tab.tcp_table.sortItems(2, Qt.SortOrder.AscendingOrder)
    assert tab._cell(tab.tcp_table, 0, 2) == "9"
    assert tab._cell(tab.tcp_table, 1, 2) == "100"

    tab.tcp_table.sortItems(7, Qt.SortOrder.DescendingOrder)
    assert tab._cell(tab.tcp_table, 0, 7) == "100%"
