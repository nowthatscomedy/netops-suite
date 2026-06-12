from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QGroupBox, QMessageBox, QSplitter, QTableWidget, QTabWidget

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


def _show_compact_file_transfer_tab(tab: DiagnosticsTab, qapp) -> None:
    tab.show()
    tab.select_diagnostic_tab("transfer")
    qapp.processEvents()

    compact_width = max(640, tab.file_transfer_page_stack.minimumSizeHint().width())
    tab.resize(compact_width, 640)
    qapp.processEvents()


def _assert_widget_readable(widget, tab: DiagnosticsTab) -> None:
    assert widget.isVisibleTo(tab)
    expected_height = widget.minimumHeight() or widget.sizeHint().height()
    expected_height = min(expected_height, widget.maximumHeight())
    assert widget.height() >= expected_height


def _assert_table_min_visible_rows(table: QTableWidget, rows: int = 4) -> None:
    row_height = max(table.verticalHeader().defaultSectionSize(), 22)
    expected_height = table.horizontalHeader().height() + row_height * rows
    assert table.minimumHeight() >= expected_height


def _assert_plain_text_edit_min_visible_lines(edit, lines: int = 5) -> None:
    expected_height = edit.fontMetrics().lineSpacing() * lines + 24
    assert max(edit.minimumHeight(), edit.height()) >= expected_height
    assert edit.maximumHeight() >= edit.minimumHeight()


def _assert_vertical_widgets_do_not_overlap(upper, lower) -> None:
    upper_bottom = upper.mapToGlobal(upper.rect().bottomLeft()).y()
    lower_top = lower.mapToGlobal(lower.rect().topLeft()).y()
    assert upper_bottom < lower_top


def _assert_widget_center_inside(parent, child) -> None:
    center = child.mapToGlobal(child.rect().center())
    assert parent.rect().contains(parent.mapFromGlobal(center))


def _global_rect(widget) -> QRect:
    return QRect(widget.mapToGlobal(widget.rect().topLeft()), widget.size())


def _assert_widgets_do_not_overlap(first, second) -> None:
    assert first.isVisibleTo(first.window())
    assert second.isVisibleTo(second.window())
    assert not _global_rect(first).intersects(_global_rect(second))


def _assert_widget_rect_inside(parent, child) -> None:
    for point in (
        child.rect().topLeft(),
        child.rect().topRight(),
        child.rect().bottomLeft(),
        child.rect().bottomRight(),
    ):
        assert parent.rect().contains(parent.mapFromGlobal(child.mapToGlobal(point)))


def _assert_input_group_height_is_compact(group, tab: DiagnosticsTab) -> None:
    max_group_height = tab.height() // 2
    assert group.minimumHeight() <= max_group_height
    assert group.height() <= max_group_height


def _assert_explicit_white_background_style(*widgets) -> None:
    combined_style = "\n".join(widget.styleSheet() for widget in widgets).replace(" ", "").lower()
    assert any(
        token in combined_style
        for token in (
            "background:#ffffff",
            "background-color:#ffffff",
            "background:#fff",
            "background-color:#fff",
            "background:white",
            "background-color:white",
        )
    )


def _assert_splitter_ratio(
    splitter: QSplitter,
    min_first: int = 1,
    min_second: int = 1,
    *,
    first_larger: bool = True,
) -> None:
    sizes = splitter.sizes()
    assert len(sizes) >= 2
    assert sizes[0] >= min_first
    assert sizes[1] >= min_second
    if first_larger:
        assert sizes[0] > sizes[1]


def _assert_current_file_transfer_page_readable(tab: DiagnosticsTab, expected_index: int) -> None:
    stack = tab.file_transfer_page_stack
    current_page = stack.currentWidget()

    assert stack.currentIndex() == expected_index
    assert current_page.isVisibleTo(tab)
    assert tab.file_transfer_scroll_area.widget() is stack
    assert tab.file_transfer_scroll_area.widgetResizable()
    assert stack.minimumHeight() == 0
    assert current_page.minimumHeight() == 0
    assert current_page.sizeHint().height() >= current_page.minimumSizeHint().height()


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

    assert set(saved) == {"current_tool_key", "tools", "ping", "tcp", "dns", "trace", "ftp", "iperf"}
    assert saved["current_tool_key"] == "ping"
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

    assert tab._current_tool_key() == "transfer"
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


def test_diagnostics_sidebar_labels_navigation_and_legacy_tools_migration(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))

    expected_labels = [
        "Ping",
        "포트 확인 (TCPing)",
        "DNS 조회 (nslookup)",
        "경로 추적 (tracert/pathping)",
        "대역폭 측정 (iperf3)",
        "같은 대역 장비 찾기 (ARP 스캔)",
        "서브넷 계산기",
        "MAC 제조사 조회 (OUI)",
        "파일 전송",
        "명령 출력",
    ]
    assert [tab.diagnostic_tool_list.item(index).text() for index in range(tab.diagnostic_tool_list.count())] == expected_labels
    assert not tab.findChildren(QTabWidget)

    assert tab.select_diagnostic_tab("dns")
    assert tab._current_tool_key() == "dns"
    assert tab.diagnostic_tool_list.currentRow() == 2

    assert tab.select_quick_tool("arp")
    assert tab._current_tool_key() == "arp"
    assert tab.diagnostic_tool_list.currentRow() == 5

    tab.restore_ui_state({"current_tab": 0, "tools": {"version": 2, "current_subtab": 2}})
    assert tab._current_tool_key() == "subnet"

    tab.restore_ui_state({"current_tab": 0, "tools": {"version": 1, "current_subtab": 2}})
    assert tab._current_tool_key() == "oui"


def test_ping_tcp_target_inputs_are_readable_and_explain_multi_target_format(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.show()
    tab.resize(1280, 720)
    qapp.processEvents()

    tab.select_diagnostic_tab("ping")
    qapp.processEvents()
    _assert_plain_text_edit_min_visible_lines(tab.ping_targets_edit, lines=4)
    _assert_vertical_widgets_do_not_overlap(tab.ping_targets_edit, tab.ping_targets_help_label)
    _assert_vertical_widgets_do_not_overlap(tab.ping_targets_help_label, tab.ping_count_edit)
    _assert_vertical_widgets_do_not_overlap(tab.ping_start_button, tab.ping_empty_label)
    _assert_widget_rect_inside(tab.ping_input_group, tab.ping_continuous_check)
    _assert_widget_rect_inside(tab.ping_input_group, tab.ping_start_button)
    _assert_widget_rect_inside(tab.ping_input_group, tab.ping_cancel_button)

    tab.select_diagnostic_tab("tcp")
    qapp.processEvents()
    _assert_plain_text_edit_min_visible_lines(tab.tcp_targets_edit, lines=3)
    _assert_vertical_widgets_do_not_overlap(tab.tcp_targets_edit, tab.tcp_targets_help_label)
    _assert_vertical_widgets_do_not_overlap(tab.tcp_targets_help_label, tab.tcp_ports_edit)
    _assert_vertical_widgets_do_not_overlap(tab.tcp_ports_edit, tab.tcp_ports_help_label)
    _assert_vertical_widgets_do_not_overlap(tab.tcp_ports_help_label, tab.tcp_count_edit)
    _assert_vertical_widgets_do_not_overlap(tab.tcp_start_button, tab.tcp_empty_label)
    _assert_widget_rect_inside(tab.tcp_input_group, tab.tcp_continuous_check)
    _assert_widget_rect_inside(tab.tcp_input_group, tab.tcp_start_button)
    _assert_widget_rect_inside(tab.tcp_input_group, tab.tcp_cancel_button)

    assert "192.168.0.254" in tab.ping_targets_edit.placeholderText()
    assert "192.168.0.254" in tab.tcp_targets_edit.placeholderText()

    for label in (tab.ping_targets_help_label, tab.tcp_targets_help_label):
        text = label.text()
        assert "한 줄에 하나" in text
        assert "이름,IP" in text
        assert "생략하면 대상 주소가 이름" in text

    ports_help = tab.tcp_ports_help_label.text()
    assert "22,80,443" in ports_help
    assert "8000-8010" in ports_help
    assert "대상 × 포트" in ports_help

    ping_targets = "GW,192.168.0.1\nDNS,8.8.8.8\n192.168.0.254"
    tcp_targets = "API,10.0.0.10\nDB,10.0.0.20\n10.0.0.30"
    tab.ping_targets_edit.setPlainText(ping_targets)
    tab.tcp_targets_edit.setPlainText(tcp_targets)
    tab.tcp_ports_edit.setText("22,80,443 8000-8010")
    saved = tab.save_ui_state()

    restored = DiagnosticsTab(build_fake_state(tmp_path))
    restored.restore_ui_state(saved)
    assert restored.ping_targets_edit.toPlainText() == ping_targets
    assert restored.tcp_targets_edit.toPlainText() == tcp_targets
    assert restored.tcp_ports_edit.text() == "22,80,443 8000-8010"


@pytest.mark.parametrize(("width", "height"), [(1280, 720), (1120, 720), (900, 640)])
def test_ping_tcp_primary_controls_fit_without_overlap(qapp, tmp_path, width, height):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.show()
    tab.resize(width, height)
    qapp.processEvents()

    tab.select_diagnostic_tab("ping")
    qapp.processEvents()
    _assert_input_group_height_is_compact(tab.ping_input_group, tab)
    for button in (tab.ping_start_button, tab.ping_cancel_button):
        _assert_widget_rect_inside(tab.ping_input_group, button)
        _assert_widgets_do_not_overlap(tab.ping_targets_edit, button)
        _assert_widgets_do_not_overlap(button, tab.ping_empty_label)
    _assert_widgets_do_not_overlap(tab.ping_targets_help_label, tab.ping_targets_edit)
    _assert_widgets_do_not_overlap(tab.ping_targets_help_label, tab.ping_empty_label)
    assert tab.ping_empty_label.isVisibleTo(tab)

    tab.select_diagnostic_tab("tcp")
    qapp.processEvents()
    _assert_input_group_height_is_compact(tab.tcp_input_group, tab)
    for button in (tab.tcp_start_button, tab.tcp_cancel_button):
        _assert_widget_rect_inside(tab.tcp_input_group, button)
        _assert_widgets_do_not_overlap(tab.tcp_ports_edit, button)
        _assert_widgets_do_not_overlap(button, tab.tcp_empty_label)
    _assert_widgets_do_not_overlap(tab.tcp_targets_help_label, tab.tcp_ports_help_label)
    _assert_widgets_do_not_overlap(tab.tcp_ports_help_label, tab.tcp_targets_edit)
    _assert_widgets_do_not_overlap(tab.tcp_targets_edit, tab.tcp_ports_edit)
    _assert_widgets_do_not_overlap(tab.tcp_targets_help_label, tab.tcp_empty_label)
    _assert_widgets_do_not_overlap(tab.tcp_ports_help_label, tab.tcp_empty_label)
    assert tab.tcp_empty_label.isVisibleTo(tab)


def test_tcp_scroll_area_declares_explicit_white_background(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.show()
    tab.resize(1280, 720)
    tab.select_diagnostic_tab("tcp")
    qapp.processEvents()

    assert tab.tcp_scroll_area.widget() is tab.tcp_page_content
    _assert_explicit_white_background_style(
        tab.tcp_scroll_area,
        tab.tcp_scroll_area.viewport(),
        tab.tcp_page_content,
    )


@pytest.mark.parametrize(
    ("mode_index", "protocol", "expected_stack_index", "representative_controls"),
    [
        (
            0,
            "ftp",
            0,
            (
                "ftp_client_protocol_combo",
                "ftp_client_host_edit",
                "ftp_client_connect_button",
                "ftp_remote_table",
                "ftp_transfer_table",
                "ftp_client_log_output",
            ),
        ),
        (
            0,
            "ftps",
            0,
            (
                "ftp_client_protocol_combo",
                "ftp_client_host_edit",
                "ftp_client_connect_button",
                "ftp_remote_table",
                "ftp_transfer_table",
                "ftp_client_log_output",
            ),
        ),
        (
            0,
            "sftp",
            0,
            (
                "ftp_client_protocol_combo",
                "ftp_client_host_edit",
                "ftp_client_connect_button",
                "ftp_remote_table",
                "ftp_transfer_table",
                "ftp_client_log_output",
            ),
        ),
        (
            1,
            None,
            1,
            (
                "scp_client_host_edit",
                "scp_client_upload_button",
                "scp_transfer_table",
                "scp_client_log_output",
            ),
        ),
        (
            2,
            None,
            2,
            (
                "tftp_client_host_edit",
                "tftp_client_upload_button",
                "tftp_transfer_table",
                "tftp_client_log_output",
            ),
        ),
    ],
)
def test_file_transfer_client_pages_remain_readable_at_compact_size(
    qapp,
    tmp_path,
    mode_index,
    protocol,
    expected_stack_index,
    representative_controls,
):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    _show_compact_file_transfer_tab(tab, qapp)

    tab.file_transfer_role_combo.setCurrentIndex(0)
    tab.file_transfer_mode_combo.setCurrentIndex(mode_index)
    if protocol is not None:
        protocol_index = tab.ftp_client_protocol_combo.findData(protocol)
        assert protocol_index >= 0
        tab.ftp_client_protocol_combo.setCurrentIndex(protocol_index)
    qapp.processEvents()

    assert tab.file_transfer_role_combo.currentData() == 0
    assert tab.file_transfer_mode_combo.currentIndex() == mode_index
    if protocol is not None:
        assert tab.ftp_client_protocol_combo.currentData() == protocol

    _assert_current_file_transfer_page_readable(tab, expected_stack_index)
    for control_name in representative_controls:
        _assert_widget_readable(getattr(tab, control_name), tab)


def test_file_transfer_preflight_confirmation_cancels_and_accepts(qapp, tmp_path, monkeypatch):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    captured: list[str] = []

    def fake_cancel(self):
        captured.append(self.text())
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "exec", fake_cancel)
    assert not tab._confirm_transfer_preflight(
        protocol="FTP",
        direction="업로드",
        source="startup.cfg",
        target="/backup",
        file_count=1,
        overwrite_note="같은 이름이면 덮어쓸 수 있습니다.",
    )
    assert "프로토콜: FTP" in captured[-1]
    assert "방향: 업로드" in captured[-1]
    assert "덮어쓰기 가능성: 같은 이름이면 덮어쓸 수 있습니다." in captured[-1]

    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    assert tab._confirm_transfer_preflight(
        protocol="SCP",
        direction="다운로드",
        source="/var/log/messages",
        target=str(tmp_path),
        file_count=1,
        overwrite_note="같은 이름이면 덮어쓸 수 있습니다.",
    )


def test_arp_scan_result_table_keeps_readable_height(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    _show_compact_file_transfer_tab(tab, qapp)
    tab.select_diagnostic_tab("arp")
    qapp.processEvents()

    assert tab.arp_table.minimumHeight() >= 220
    assert tab.arp_output.minimumHeight() >= 90
    assert tab.arp_output.maximumHeight() > 1000
    assert tab.arp_result_splitter.sizes()[0] > tab.arp_result_splitter.sizes()[1]


def test_diagnostics_result_tables_keep_readable_height_and_table_first_splitters(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.show()
    tab.resize(1280, 720)
    qapp.processEvents()

    tab.select_diagnostic_tab("ping")
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.ping_table)
    _assert_splitter_ratio(tab.ping_splitter)

    tab.select_diagnostic_tab("tcp")
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.tcp_table)
    _assert_splitter_ratio(tab.tcp_splitter)

    tab.select_diagnostic_tab("trace")
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.trace_table)
    _assert_splitter_ratio(tab.trace_splitter)

    tab.select_diagnostic_tab("subnet")
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.subnet_calc_detail_table)

    tab.select_diagnostic_tab("oui")
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.oui_table)
    _assert_splitter_ratio(tab.oui_result_splitter)


def test_quick_tools_remove_symptom_shortcuts_and_subnet_results_are_progressive(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.select_diagnostic_tab("subnet")
    qapp.processEvents()

    group_titles = [group.title() for group in tab.findChildren(QGroupBox)]
    assert "증상별 바로가기" not in group_titles
    assert not tab.subnet_calc_empty_label.isHidden()
    assert tab.subnet_calc_summary_widget.isHidden()
    assert tab.subnet_calc_result_hint.isHidden()
    assert tab.subnet_calc_detail_table.isHidden()

    tab.subnet_calc_ip_edit.setText("192.168.10.5")
    tab.subnet_calc_prefix_edit.setText("24")
    tab.calculate_subnet_from_tools_inputs()

    assert tab.subnet_calc_empty_label.isHidden()
    assert not tab.subnet_calc_summary_widget.isHidden()
    assert not tab.subnet_calc_result_hint.isHidden()
    assert not tab.subnet_calc_detail_table.isHidden()
    assert tab.subnet_calc_detail_table.rowCount() > 0


def test_file_transfer_tables_have_empty_states_and_table_first_splitters(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    _show_compact_file_transfer_tab(tab, qapp)

    tab.file_transfer_role_combo.setCurrentIndex(0)
    tab.file_transfer_mode_combo.setCurrentIndex(0)
    qapp.processEvents()
    assert tab.ftp_remote_table.minimumHeight() >= 160
    assert tab.ftp_remote_table.maximumHeight() > 1000
    _assert_table_min_visible_rows(tab.ftp_transfer_table)
    _assert_splitter_ratio(tab.ftp_client_main_splitter, first_larger=False)
    _assert_splitter_ratio(tab.ftp_client_result_log_splitter)
    assert not tab.ftp_transfer_empty_label.isHidden()

    tab.file_transfer_mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.scp_transfer_table)
    _assert_splitter_ratio(tab.scp_client_result_log_splitter)
    assert not tab.scp_transfer_empty_label.isHidden()

    tab.file_transfer_mode_combo.setCurrentIndex(2)
    qapp.processEvents()
    _assert_table_min_visible_rows(tab.tftp_transfer_table)
    _assert_splitter_ratio(tab.tftp_client_result_log_splitter)
    assert not tab.tftp_transfer_empty_label.isHidden()

    tab.file_transfer_role_combo.setCurrentIndex(1)
    qapp.processEvents()
    _assert_splitter_ratio(tab.ftp_server_splitter, first_larger=False)
    tab.file_transfer_mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    _assert_splitter_ratio(tab.scp_server_splitter, first_larger=False)
    tab.file_transfer_mode_combo.setCurrentIndex(2)
    qapp.processEvents()
    _assert_splitter_ratio(tab.tftp_server_splitter, first_larger=False)


def test_file_transfer_checkboxes_keep_visible_indicator_when_checked(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    _show_compact_file_transfer_tab(tab, qapp)

    tab.file_transfer_role_combo.setCurrentIndex(0)
    tab.file_transfer_mode_combo.setCurrentIndex(0)
    qapp.processEvents()

    tab.file_transfer_role_combo.setCurrentIndex(1)
    tab.file_transfer_mode_combo.setCurrentIndex(0)
    ftp_index = tab.ftp_server_protocol_combo.findData("ftp")
    if ftp_index >= 0:
        tab.ftp_server_protocol_combo.setCurrentIndex(ftp_index)
    qapp.processEvents()

    tab.file_transfer_mode_combo.setCurrentIndex(2)
    qapp.processEvents()

    checkboxes = [
        tab.ftp_client_passive_check,
        tab.ftp_server_readonly_check,
        tab.ftp_server_anonymous_check,
        tab.tftp_server_readonly_check,
    ]
    for checkbox in checkboxes:
        checkbox.setEnabled(True)
        checkbox.setChecked(True)
        qapp.processEvents()
        style = checkbox.styleSheet()
        assert checkbox.isChecked()
        assert "QCheckBox::indicator:checked" in style
        assert "background: #475467" in style
        assert checkbox.minimumHeight() >= 24


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
