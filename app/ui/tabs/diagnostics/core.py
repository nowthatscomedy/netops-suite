from __future__ import annotations

import csv
import ipaddress
import re
from datetime import datetime
from threading import Event
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.models.network_models import PublicIperfServer
from app.models.result_models import PingResult, TcpCheckResult
from app.ui.common import JobRunner, make_step_hint, nullable_number_sort_value, sortable_table_item
from app.ui.tabs.diagnostics.dns import DnsDiagnosticsMixin
from app.ui.tabs.diagnostics.ftp import FtpDiagnosticsMixin
from app.ui.tabs.diagnostics.iperf import IperfDiagnosticsMixin
from app.ui.tabs.diagnostics.ping import PingDiagnosticsMixin
from app.ui.tabs.diagnostics.result_dock import ResultDockMixin
from app.ui.tabs.diagnostics.scp import ScpDiagnosticsMixin
from app.ui.tabs.diagnostics.tcp import TcpDiagnosticsMixin
from app.ui.tabs.diagnostics.tftp import TftpDiagnosticsMixin
from app.ui.tabs.diagnostics.tools import ToolsDiagnosticsMixin
from app.ui.tabs.diagnostics.trace import TraceDiagnosticsMixin
from app.utils.file_utils import timestamped_export_path
from app.utils.validators import ValidationError, parse_positive_int, validate_host_input
from netops_suite.ui.actions import ActionKind, make_action_button


class DiagnosticsTab(
    ResultDockMixin,
    PingDiagnosticsMixin,
    TcpDiagnosticsMixin,
    DnsDiagnosticsMixin,
    TraceDiagnosticsMixin,
    ToolsDiagnosticsMixin,
    FtpDiagnosticsMixin,
    IperfDiagnosticsMixin,
    ScpDiagnosticsMixin,
    TftpDiagnosticsMixin,
    QWidget,
):
    result_dock_visibility_changed = Signal(str, bool)

    DNS_TYPES = [
        ("A - IPv4 주소", "A", "도메인의 IPv4 주소를 조회합니다."),
        ("AAAA - IPv6 주소", "AAAA", "도메인의 IPv6 주소를 조회합니다."),
        ("CNAME - 별칭", "CNAME", "도메인이 연결된 별칭 레코드를 조회합니다."),
        ("MX - 메일 서버", "MX", "메일 서버 레코드를 조회합니다."),
        ("PTR - 역방향 조회", "PTR", "IP 주소를 도메인 이름으로 조회합니다."),
    ]

    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._job_runner = JobRunner(self.state.thread_pool, self)
        self._active_workers = self._job_runner._active_workers
        self._shutting_down = False
        self._floating_result_docks = {"ping": None, "tcp": None}
        self._result_hosts: dict[str, QWidget] = {}
        self._result_host_layouts: dict[str, QVBoxLayout] = {}
        self._result_panels: dict[str, QWidget] = {}
        self._result_placeholders: dict[str, QLabel] = {}
        self._result_splitters: dict[str, object] = {}

        self.ping_results: list[PingResult] = []
        self.tcp_results: list[TcpCheckResult] = []
        self.ping_cancel_event: Event | None = None
        self.tcp_cancel_event: Event | None = None
        self.trace_cancel_event: Event | None = None
        self.arp_cancel_event: Event | None = None
        self.iperf_cancel_event: Event | None = None
        self.iperf_manage_cancel_event: Event | None = None
        self.ftp_client_cancel_event: Event | None = None
        self.ftp_server_cancel_event: Event | None = None
        self.scp_client_cancel_event: Event | None = None
        self.scp_server_cancel_event: Event | None = None
        self.tftp_client_cancel_event: Event | None = None
        self.tftp_server_cancel_event: Event | None = None

        self.ping_row_map: dict[tuple[str, str], int] = {}
        self.tcp_row_map: dict[tuple[str, str, int], int] = {}
        self.trace_row_map: dict[int, int] = {}
        self.arp_subnet_candidates: list[str] = []
        self._arp_scan_history: dict[str, dict[str, str]] = {}
        self._current_arp_scan_subnet = ""
        self.ping_log_lines: dict[tuple[str, str], list[str]] = {}
        self.tcp_log_lines: dict[tuple[str, str, int], list[str]] = {}
        self._iperf_available = False
        self._iperf_manage_available = False
        self._iperf_manage_enabled = False
        self._public_iperf_refresh_in_progress = False
        self._preferred_public_iperf_key = ""
        self._preferred_public_iperf_region = ""
        self.public_iperf_all_servers: list[PublicIperfServer] = []
        self.public_iperf_servers: list[PublicIperfServer] = []
        self._public_iperf_fetched_at = ""
        self._public_iperf_from_cache = False
        self._public_iperf_stale = True
        self._startup_activated = False
        self._tools_startup_requested = False
        self._iperf_startup_requested = False

        self.fixed_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self._build_ui()

    def start_initial_refresh(self) -> None:
        self._startup_activated = True
        self._start_tool_initialization(self._current_tool_key())

    def _handle_tool_changed(self, index: int) -> None:
        if not 0 <= index < len(self._diagnostic_tool_keys):
            return
        if self.diagnostic_stack.currentIndex() != index:
            self.diagnostic_stack.setCurrentIndex(index)
        if not self._startup_activated:
            return
        self._start_tool_initialization(self._diagnostic_tool_keys[index])

    def _sync_tool_list_to_stack(self, index: int) -> None:
        if 0 <= index < self.diagnostic_tool_list.count() and self.diagnostic_tool_list.currentRow() != index:
            self.diagnostic_tool_list.setCurrentRow(index)

    def _start_tool_initialization(self, key: str) -> None:
        if key in {"commands", "arp", "subnet", "oui"}:
            self._initialize_tools_tab()
            return
        if key == "iperf":
            self._initialize_iperf_tab()

    def _initialize_tools_tab(self) -> None:
        if self._tools_startup_requested:
            return
        self._tools_startup_requested = True
        self.refresh_arp_subnets()

    def _initialize_iperf_tab(self) -> None:
        if self._iperf_startup_requested:
            return
        self._iperf_startup_requested = True
        self.refresh_iperf_availability(deep_check=False)
        self._reset_public_iperf_server_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        layout.addWidget(make_step_hint("작업 흐름: 도구 선택, 대상 입력, 실행, 결과 저장"), 0)
        layout.addWidget(self._build_quick_diagnostics_bar(), 0)

        self.diagnostic_tool_list = QListWidget()
        self.diagnostic_tool_list.setObjectName("diagnosticToolList")
        self.diagnostic_tool_list.setMinimumWidth(200)
        self.diagnostic_tool_list.setMaximumWidth(320)
        self.diagnostic_tool_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.diagnostic_tool_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.diagnostic_tool_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.diagnostic_tool_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.diagnostic_tool_list.setUniformItemSizes(True)
        self.diagnostic_stack = QStackedWidget()
        self.diagnostic_stack.setObjectName("diagnosticToolStack")
        self.diagnostic_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.tab_widget = self.diagnostic_stack

        self._diagnostic_tool_keys: list[str] = []
        self._diagnostic_tool_index_by_key: dict[str, int] = {}
        self._diagnostic_tool_labels: dict[str, str] = {}
        tool_pages = [
            ("ping", "Ping", self._build_ping_tab),
            ("tcp", "포트 확인 (TCPing)", self._build_tcp_tab),
            ("dns", "DNS 조회 (nslookup)", self._build_dns_tab),
            ("trace", "경로 추적 (tracert/pathping)", self._build_trace_tab),
            ("iperf", "대역폭 측정 (iperf3)", self._build_iperf_tab),
            ("arp", "같은 대역 장비 찾기 (ARP 스캔)", self._build_arp_scan_page),
            ("subnet", "서브넷 계산기", self._build_subnet_calc_page),
            ("oui", "MAC 제조사 조회 (OUI)", self._build_oui_lookup_page),
            ("transfer", "파일전송(FTP/SCP)", self._build_ftp_tab),
            ("commands", "명령 출력", self._build_command_tools_page),
        ]
        for key, label, builder in tool_pages:
            self._add_diagnostic_tool(key, builder(), label)
        self._refresh_oui_status_labels()

        self.diagnostic_tool_list.hide()
        layout.addWidget(self.diagnostic_stack, 1)

        self.diagnostic_tool_list.currentRowChanged.connect(self._handle_tool_changed)
        self.diagnostic_stack.currentChanged.connect(self._sync_tool_list_to_stack)
        self.diagnostic_tool_list.setCurrentRow(0)

    def _build_quick_diagnostics_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("quickDiagnosticsBar")
        bar.setStyleSheet(
            """
            QFrame#quickDiagnosticsBar {
                background:#ffffff;
                border:1px solid #e4e7ec;
                border-radius:4px;
            }
            QLabel#quickDiagnosticsTitle {
                color:#344054;
                font-weight:700;
            }
            QLabel#quickDiagnosticsStatus {
                color:#667085;
            }
            """
        )
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)

        title = QLabel("빠른 진단")
        title.setObjectName("quickDiagnosticsTitle")
        self.quick_target_edit = QLineEdit()
        self.quick_target_edit.setObjectName("quickDiagnosticsTarget")
        self.quick_target_edit.setPlaceholderText("IP/호스트 또는 CIDR")
        self.quick_target_edit.setMinimumWidth(240)
        self.quick_target_edit.setToolTip("예: 8.8.8.8, 192.168.0.1:443, 192.168.0.10/24")
        self.quick_port_edit = QLineEdit()
        self.quick_port_edit.setObjectName("quickDiagnosticsPort")
        self.quick_port_edit.setPlaceholderText("포트")
        self.quick_port_edit.setMaximumWidth(72)
        self.quick_port_edit.setToolTip("TCPing/iperf에 사용할 포트입니다.")
        self.quick_status_label = QLabel("준비")
        self.quick_status_label.setObjectName("quickDiagnosticsStatus")
        self.quick_status_label.setWordWrap(True)
        self.quick_status_label.setMinimumWidth(140)
        self.quick_status_label.setMaximumWidth(260)

        input_row.addWidget(title)
        input_row.addWidget(self.quick_target_edit, 1)
        input_row.addWidget(self.quick_port_edit)
        input_row.addWidget(self.quick_status_label)
        layout.addLayout(input_row)

        action_grid = QGridLayout()
        action_grid.setContentsMargins(0, 0, 0, 0)
        action_grid.setHorizontalSpacing(6)
        action_grid.setVerticalSpacing(4)
        quick_actions = [
            ("quick_ping_button", "Ping", ActionKind.START, self.run_quick_ping, "입력한 대상으로 Ping을 실행합니다."),
            ("quick_tcp_button", "TCPing", ActionKind.START, self.run_quick_tcp_check, "입력한 대상과 포트로 TCP 연결을 확인합니다."),
            ("quick_dns_button", "DNS", ActionKind.PRIMARY, self.run_quick_dns_lookup, "입력한 도메인/IP를 nslookup으로 조회합니다."),
            ("quick_tracert_button", "tracert -d", ActionKind.UTILITY, self.run_quick_tracert_no_resolve, "DNS 역조회 없이 tracert를 실행합니다."),
            ("quick_pathping_button", "pathping -n", ActionKind.UTILITY, self.run_quick_pathping_no_resolve, "DNS 역조회 없이 pathping을 실행합니다."),
            ("quick_iperf_button", "iperf3", ActionKind.START, self.run_quick_iperf_client, "입력한 서버와 포트로 iperf3 클라이언트를 실행합니다."),
            ("quick_arp_scan_button", "ARP 스캔", ActionKind.START, self.run_quick_arp_scan, "입력한 IPv4 CIDR 대역을 스캔합니다."),
            ("quick_subnet_button", "서브넷 계산기", ActionKind.PRIMARY, self.run_quick_subnet, "입력한 CIDR 또는 IP/Prefix를 계산합니다."),
            ("quick_oui_button", "OUI", ActionKind.PRIMARY, self.run_quick_oui_lookup, "입력한 MAC 주소의 제조사를 조회합니다."),
            ("quick_public_ip_button", "공인 IP", ActionKind.UTILITY, self.run_quick_public_ip, "현재 공인 IP를 확인합니다."),
            ("quick_snapshot_button", "인터페이스", ActionKind.UTILITY, self.run_quick_interface_snapshot, "현재 인터페이스 정보를 불러옵니다."),
            ("quick_ipconfig_button", "ipconfig", ActionKind.UTILITY, self.run_quick_ipconfig, "ipconfig /all을 실행합니다."),
            ("quick_route_button", "route", ActionKind.UTILITY, self.run_quick_route_print, "route print를 실행합니다."),
            ("quick_arp_table_button", "arp -a", ActionKind.UTILITY, self.run_quick_arp_table, "arp -a를 실행합니다."),
            ("quick_flush_dns_button", "DNS 캐시", ActionKind.DANGER, self.run_quick_flush_dns_cache, "Windows DNS 캐시를 비웁니다."),
            ("quick_transfer_button", "파일전송(FTP/SCP)", ActionKind.UTILITY, self.run_quick_file_transfer, "FTP/SCP 파일 전송 도구로 이동합니다."),
        ]
        self.quick_action_buttons = []
        for index, (attr_name, text, kind, handler, tooltip) in enumerate(quick_actions):
            button = make_action_button(text, kind, tooltip=tooltip)
            setattr(self, attr_name, button)
            self.quick_action_buttons.append(button)
            action_grid.addWidget(button, index // 8, index % 8)
        action_grid.setColumnStretch(7, 1)
        layout.addLayout(action_grid)

        self.quick_target_edit.returnPressed.connect(self.run_quick_ping)
        for button, (_attr_name, _text, _kind, handler, _tooltip) in zip(self.quick_action_buttons, quick_actions):
            button.clicked.connect(handler)
        return bar

    def _add_diagnostic_tool(self, key: str, widget: QWidget, label: str) -> None:
        index = len(self._diagnostic_tool_keys)
        item = QListWidgetItem(label)
        item.setToolTip(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.diagnostic_tool_list.addItem(item)
        self.diagnostic_stack.addWidget(widget)
        self._diagnostic_tool_keys.append(key)
        self._diagnostic_tool_index_by_key[key] = index
        self._diagnostic_tool_labels[key] = label

    def run_quick_ping(self) -> None:
        self.select_diagnostic_tab("ping")
        target = self._quick_host_target()
        if not target:
            return
        if not self.ping_start_button.isEnabled():
            self._set_quick_status("Ping이 이미 실행 중입니다.", "warning")
            return

        self.ping_targets_edit.setPlainText(target)
        self._set_quick_status(f"Ping 실행: {target}", "success")
        self.start_ping()

    def run_quick_tcp_check(self) -> None:
        self.select_diagnostic_tab("tcp")
        target, port = self._quick_host_and_port()
        if not target:
            return
        if not port:
            self.tcp_targets_edit.setPlainText(target)
            self.quick_port_edit.setFocus()
            self._set_quick_status("TCPing에는 포트가 필요합니다.", "warning")
            return
        if not self.tcp_start_button.isEnabled():
            self._set_quick_status("TCPing이 이미 실행 중입니다.", "warning")
            return

        self.tcp_targets_edit.setPlainText(target)
        self.tcp_ports_edit.setText(port)
        self._set_quick_status(f"TCPing 실행: {target}:{port}", "success")
        self.start_tcp_check()

    def run_quick_dns_lookup(self) -> None:
        self.select_diagnostic_tab("dns")
        target = self._quick_host_target()
        if not target:
            return
        if not self.dns_run_button.isEnabled():
            self._set_quick_status("DNS 조회가 이미 실행 중입니다.", "warning")
            return

        self.dns_query_edit.setText(target)
        self._set_quick_status(f"DNS 조회: {target}", "success")
        self.run_dns_lookup()

    def run_quick_tracert_no_resolve(self) -> None:
        self.select_diagnostic_tab("trace")
        target = self._quick_host_target()
        if not target:
            return
        if not self.tracert_button.isEnabled():
            self._set_quick_status("경로 추적이 이미 실행 중입니다.", "warning")
            return

        self.trace_target_edit.setText(target)
        self.trace_no_resolve_check.setChecked(True)
        self._set_quick_status(f"tracert -d 실행: {target}", "success")
        self.start_trace("tracert")

    def run_quick_pathping_no_resolve(self) -> None:
        self.select_diagnostic_tab("trace")
        target = self._quick_host_target()
        if not target:
            return
        if not self.pathping_button.isEnabled():
            self._set_quick_status("경로 추적이 이미 실행 중입니다.", "warning")
            return

        self.trace_target_edit.setText(target)
        self.trace_no_resolve_check.setChecked(True)
        self._set_quick_status(f"pathping -n 실행: {target}", "success")
        self.start_trace("pathping")

    def run_quick_iperf_client(self) -> None:
        self.select_diagnostic_tab("iperf")
        target, port = self._quick_host_and_port(default_port="5201")
        if not target:
            return
        if not self.iperf_run_button.isEnabled():
            self._set_quick_status("iperf3를 실행할 수 없는 상태입니다.", "warning")
            return

        client_index = self.iperf_mode_combo.findData("client")
        if client_index >= 0:
            self.iperf_mode_combo.setCurrentIndex(client_index)
        self.iperf_use_public_server_check.setChecked(False)
        self.iperf_server_edit.setText(target)
        self.iperf_port_edit.setText(port or "5201")
        self._set_quick_status(f"iperf3 실행: {target}:{port or '5201'}", "success")
        self.run_iperf_test()

    def run_quick_arp_scan(self) -> None:
        self.select_diagnostic_tab("arp")
        payload = self._quick_payload_text()
        if not payload:
            return

        subnet_text = self._quick_subnet_cidr(payload)
        if not subnet_text:
            self._set_quick_status("ARP 스캔에는 CIDR 대역이 필요합니다.", "warning")
            return
        if not self.arp_start_button.isEnabled():
            self._set_quick_status("ARP 스캔이 이미 실행 중입니다.", "warning")
            return

        self.arp_subnet_edit.setText(subnet_text)
        self._set_quick_status(f"ARP 스캔: {subnet_text}", "success")
        self.start_arp_scan()

    def run_quick_subnet(self) -> None:
        self.select_diagnostic_tab("subnet")
        payload = self._quick_payload_text()
        if not payload:
            return

        ip_text, prefix_text = self._split_quick_subnet_payload(payload)
        self.subnet_calc_ip_edit.setText(ip_text)
        if prefix_text:
            self.subnet_calc_prefix_edit.setText(prefix_text)
            self._set_quick_status(f"서브넷 계산: {ip_text}/{prefix_text}", "success")
            self.calculate_subnet_from_tools_inputs()
            return

        self.subnet_calc_prefix_edit.clear()
        self._clear_subnet_calc_results()
        self.subnet_calc_status_label.setText("Prefix 또는 서브넷 마스크를 입력하면 계산합니다.")
        self.subnet_calc_status_label.setStyleSheet("color:#475467;")
        self.subnet_calc_prefix_edit.setFocus()
        self._set_quick_status("서브넷 계산에는 Prefix가 필요합니다.", "warning")

    def run_quick_oui_lookup(self) -> None:
        self.select_diagnostic_tab("oui")
        text = self._quick_input_text()
        if not text:
            return

        self.oui_mac_edit.setPlainText(text)
        self._set_quick_status("OUI 조회", "success")
        self.lookup_oui_vendor()

    def run_quick_public_ip(self) -> None:
        self.select_diagnostic_tab("commands")
        self._set_quick_status("공인 IP 확인", "success")
        self.check_public_ip()

    def run_quick_interface_snapshot(self) -> None:
        self.select_diagnostic_tab("commands")
        self._set_quick_status("인터페이스 정보 조회", "success")
        self.load_interface_snapshot()

    def run_quick_ipconfig(self) -> None:
        self.select_diagnostic_tab("commands")
        self._set_quick_status("ipconfig /all 실행", "success")
        self._run_tools_command(self.state.trace_service.run_ipconfig_all)

    def run_quick_route_print(self) -> None:
        self.select_diagnostic_tab("commands")
        self._set_quick_status("route print 실행", "success")
        self._run_tools_command(self.state.trace_service.run_route_print)

    def run_quick_arp_table(self) -> None:
        self.select_diagnostic_tab("commands")
        self._set_quick_status("arp -a 실행", "success")
        self._run_tools_command(self.state.trace_service.run_arp_table)

    def run_quick_flush_dns_cache(self) -> None:
        self.select_diagnostic_tab("commands")
        self._set_quick_status("DNS 캐시 비우기", "warning")
        self._confirm_and_flush_dns_cache()

    def run_quick_file_transfer(self) -> None:
        self.select_diagnostic_tab("transfer")
        self._set_quick_status("파일전송(FTP/SCP)", "info")

    def _quick_input_text(self) -> str:
        text = self.quick_target_edit.text().strip()
        if text:
            return text
        self.quick_target_edit.setFocus()
        self._set_quick_status("IP 또는 호스트를 입력하세요.", "error")
        return ""

    def _quick_payload_text(self) -> str:
        text = self._quick_input_text()
        if not text:
            return ""
        first_line = text.splitlines()[0].strip()
        if "," in first_line:
            _label, value = first_line.split(",", 1)
            if value.strip():
                first_line = value.strip()
        return first_line

    def _quick_host_target(self) -> str:
        payload = self._quick_payload_text()
        if not payload:
            return ""

        target, _port = self._split_quick_host_port_payload(payload)
        try:
            return validate_host_input(target)
        except ValidationError as exc:
            self._set_quick_status(str(exc), "error")
            self.quick_target_edit.setFocus()
            return ""

    def _quick_host_and_port(self, default_port: str = "") -> tuple[str, str]:
        payload = self._quick_payload_text()
        if not payload:
            return "", ""

        target, parsed_port = self._split_quick_host_port_payload(payload)
        port = self.quick_port_edit.text().strip() or parsed_port or default_port
        try:
            target = validate_host_input(target)
        except ValidationError as exc:
            self._set_quick_status(str(exc), "error")
            self.quick_target_edit.setFocus()
            return "", ""

        if port and all(separator not in port for separator in (",", "-", " ")):
            try:
                parse_positive_int(port, "포트", minimum=1, maximum=65535)
            except ValidationError as exc:
                self._set_quick_status(str(exc), "error")
                self.quick_port_edit.setFocus()
                return "", ""
        return target, port

    def _split_quick_host_port_payload(self, payload: str) -> tuple[str, str]:
        parts = payload.split()
        target = parts[0].strip() if parts else payload.strip()
        port = parts[1].strip() if len(parts) > 1 else ""
        if "/" in target:
            target = target.split("/", 1)[0].strip()
        if ":" in target and target.count(":") == 1:
            host_part, port_part = target.rsplit(":", 1)
            if host_part and port_part:
                target = host_part.strip()
                port = port or port_part.strip()
        return target, port

    def _split_quick_subnet_payload(self, payload: str) -> tuple[str, str]:
        first_token = payload.split()[0].strip()
        if "/" in first_token:
            ip_text, prefix_text = first_token.split("/", 1)
            return ip_text.strip(), prefix_text.strip()

        parts = payload.split()
        ip_text = parts[0].strip() if parts else payload.strip()
        prefix_text = parts[1].strip() if len(parts) > 1 else ""
        return ip_text, prefix_text

    def _quick_subnet_cidr(self, payload: str) -> str:
        ip_text, prefix_text = self._split_quick_subnet_payload(payload)
        if not ip_text or not prefix_text:
            return ""
        try:
            network = ipaddress.ip_network(f"{ip_text}/{prefix_text}", strict=False)
        except ValueError:
            self._set_quick_status("유효한 IPv4 CIDR을 입력하세요.", "error")
            return ""
        if network.version != 4:
            self._set_quick_status("IPv4 CIDR만 지원합니다.", "error")
            return ""
        return network.with_prefixlen

    def _set_quick_status(self, message: str, kind: str = "info") -> None:
        colors = {
            "info": "#667085",
            "success": "#166534",
            "warning": "#92400e",
            "error": "#b42318",
        }
        self.quick_status_label.setText(message)
        self.quick_status_label.setStyleSheet(f"color:{colors.get(kind, colors['info'])};")

    def _current_tool_key(self) -> str:
        index = self.diagnostic_stack.currentIndex()
        if 0 <= index < len(self._diagnostic_tool_keys):
            return self._diagnostic_tool_keys[index]
        return "ping"

    def select_diagnostic_tab(self, key: str) -> bool:
        index = self._diagnostic_tool_index_by_key.get(key)
        if index is None:
            return False
        self.diagnostic_tool_list.setCurrentRow(index)
        self.diagnostic_stack.setCurrentIndex(index)
        return True

    def select_quick_tool(self, key: str) -> bool:
        return self.select_diagnostic_tab(key)

    def _setup_table(self, table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)

    def _set_stretch_columns(self, table: QTableWidget, *stretch_columns: int) -> None:
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        stretch_set = set(stretch_columns)
        for column in range(table.columnCount()):
            mode = QHeaderView.ResizeMode.Stretch if column in stretch_set else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)

    def _output(self) -> QPlainTextEdit:
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setFont(self.fixed_font)
        output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return output

    def _sortable_table_item(self, text: str, sort_value=None) -> QTableWidgetItem:
        return sortable_table_item(text, sort_value)

    def _capture_sort_state(self, table: QTableWidget) -> tuple[bool, int, Qt.SortOrder]:
        header = table.horizontalHeader()
        return table.isSortingEnabled(), header.sortIndicatorSection(), header.sortIndicatorOrder()

    def _restore_sort_state(self, table: QTableWidget, sort_state: tuple[bool, int, Qt.SortOrder]) -> None:
        sorting_enabled, section, order = sort_state
        if not sorting_enabled:
            return
        table.setSortingEnabled(True)
        if 0 <= section < table.columnCount():
            table.sortItems(section, order)

    def _nullable_number_sort_value(self, value: float | int | None) -> tuple[int, float]:
        return nullable_number_sort_value(value)

    def _build_subnet_metric_card(self, title: str, accent_color: str) -> tuple[QWidget, QLabel]:
        card = QWidget()
        card.setObjectName("subnetMetricCard")
        card.setStyleSheet(
            """
            QWidget#subnetMetricCard {
                background:transparent;
                border:0;
                border-bottom:1px solid #e4e7ec;
                border-radius:0;
            }
            QLabel {
                border:none;
            }
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        card.setMinimumHeight(58)

        title_label = QLabel(title)
        title_label.setStyleSheet("color:#667085; font-weight:600;")
        value_label = QLabel("-")
        value_label.setStyleSheet(f"color:{accent_color}; font-size:14px; font-weight:700;")
        value_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card, value_label

    def _clear_subnet_calc_results(self) -> None:
        if hasattr(self, "subnet_calc_summary_labels"):
            for label in self.subnet_calc_summary_labels.values():
                label.setText("-")
        if hasattr(self, "subnet_calc_summary_widget"):
            self.subnet_calc_summary_widget.hide()
        if hasattr(self, "subnet_calc_result_hint"):
            self.subnet_calc_result_hint.hide()
        if hasattr(self, "subnet_calc_empty_label"):
            self.subnet_calc_empty_label.show()
        if hasattr(self, "subnet_calc_detail_table"):
            self.subnet_calc_detail_table.setRowCount(0)
            self.subnet_calc_detail_table.hide()

    def _populate_subnet_calc_results(self, details: dict[str, str]) -> None:
        if hasattr(self, "subnet_calc_summary_widget"):
            self.subnet_calc_summary_widget.show()
        if hasattr(self, "subnet_calc_empty_label"):
            self.subnet_calc_empty_label.hide()
        self.subnet_calc_summary_labels["network_address"].setText(details["network_address"])
        self.subnet_calc_summary_labels["host_range"].setText(details["host_range"])
        self.subnet_calc_summary_labels["broadcast_address"].setText(details["broadcast_address"])
        self.subnet_calc_summary_labels["usable_hosts"].setText(details["usable_hosts"])
        self.subnet_calc_result_hint.show()
        self.subnet_calc_result_hint.setText("중요 값은 위 카드에 요약되고, 상세 값은 아래 표에서 바로 확인할 수 있습니다.")

        rows = [
            ("입력 IPv4", details["ip_address"]),
            ("Prefix 길이", f"/{details['prefix_length']}"),
            ("네트워크 주소", details["network_address"]),
            ("서브넷 마스크", details["netmask"]),
            ("와일드카드 마스크", details["wildcard_mask"]),
            ("브로드캐스트 주소", details["broadcast_address"]),
            ("사용 가능 호스트 범위", details["host_range"]),
            ("첫 사용 가능 호스트", details["first_host"]),
            ("마지막 사용 가능 호스트", details["last_host"]),
            ("사용 가능 호스트 수", details["usable_hosts"]),
            ("전체 주소 수", details["total_addresses"]),
            ("주소 유형", details["address_scope"]),
            ("비고", details["notes"]),
        ]

        self.subnet_calc_detail_table.setRowCount(len(rows))
        self.subnet_calc_detail_table.show()
        for row, (label_text, value_text) in enumerate(rows):
            label_item = QTableWidgetItem(label_text)
            label_item.setForeground(QColor("#475467"))
            label_item.setBackground(QColor("#f8fafc"))
            value_item = QTableWidgetItem(value_text)
            self.subnet_calc_detail_table.setItem(row, 0, label_item)
            self.subnet_calc_detail_table.setItem(row, 1, value_item)

    def _positive_int_or_default(
        self,
        edit: QLineEdit,
        label: str,
        default: int,
        minimum: int = 1,
        maximum: int | None = None,
    ) -> int:
        text = edit.text().strip()
        if not text:
            return default
        return parse_positive_int(text, label, minimum=minimum, maximum=maximum)

    def export_selected_ping_logs(self) -> None:
        rows = self._selected_rows(self.ping_table)
        if not rows:
            QMessageBox.warning(self, "선택 필요", "로그를 저장할 Ping 항목을 먼저 선택해 주세요.")
            return

        folder = self._make_export_dir("ping_logs")
        for row in rows:
            name = self._cell(self.ping_table, row, 0)
            target = self._cell(self.ping_table, row, 1)
            lines = self.ping_log_lines.get((name, target), [])
            (folder / f"{self._safe(name)}_{self._safe(target)}.log").write_text(
                "\n".join(lines) + ("\n" if lines else ""),
                encoding="utf-8",
            )
        QMessageBox.information(self, "로그 저장 완료", f"{len(rows)}개 로그 파일을 저장했습니다.\n{folder}")

    def export_selected_tcp_logs(self) -> None:
        rows = self._selected_rows(self.tcp_table)
        if not rows:
            QMessageBox.warning(self, "선택 필요", "로그를 저장할 TCPing 항목을 먼저 선택해 주세요.")
            return

        folder = self._make_export_dir("tcp_logs")
        for row in rows:
            name = self._cell(self.tcp_table, row, 0)
            target = self._cell(self.tcp_table, row, 1)
            port = self._cell(self.tcp_table, row, 2)
            try:
                key = (name, target, int(port))
            except ValueError:
                continue
            lines = self.tcp_log_lines.get(key, [])
            (folder / f"{self._safe(name)}_{self._safe(target)}_{port}.log").write_text(
                "\n".join(lines) + ("\n" if lines else ""),
                encoding="utf-8",
            )
        QMessageBox.information(self, "로그 저장 완료", f"{len(rows)}개 로그 파일을 저장했습니다.\n{folder}")

    def _export_table_to_csv(self, table: QTableWidget, prefix: str) -> None:
        if table.rowCount() == 0:
            QMessageBox.warning(self, "내보내기 불가", "저장할 결과가 없습니다.")
            return

        path = timestamped_export_path(self.state.paths.exports_dir, prefix, "csv")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([table.horizontalHeaderItem(column).text() for column in range(table.columnCount())])
            for row in range(table.rowCount()):
                writer.writerow([self._cell(table, row, column) for column in range(table.columnCount())])
        QMessageBox.information(self, "CSV 저장 완료", f"결과를 저장했습니다.\n{path}")

    def _make_export_dir(self, prefix: str):
        folder = self.state.paths.exports_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _selected_rows(self, table: QTableWidget) -> list[int]:
        selection_model = table.selectionModel()
        if selection_model is None:
            return []
        return sorted({index.row() for index in selection_model.selectedRows()})

    def _cell(self, table: QTableWidget, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text() if item else ""

    def _safe(self, value: str) -> str:
        return re.sub(r'[\\/:*?"<>|]+', "_", value.strip()) or "item"

    def save_ui_state(self) -> dict:
        if hasattr(self, "_collect_ftp_runtime_state") and hasattr(self.state, "save_ftp_runtime"):
            self.state.save_ftp_runtime(self._collect_ftp_runtime_state())
        if hasattr(self, "_collect_scp_runtime_state") and hasattr(self.state, "save_scp_runtime"):
            self.state.save_scp_runtime(self._collect_scp_runtime_state())
        if hasattr(self, "_collect_tftp_runtime_state") and hasattr(self.state, "save_tftp_runtime"):
            self.state.save_tftp_runtime(self._collect_tftp_runtime_state())
        return {
            "current_tool_key": self._current_tool_key(),
            "tools": {
                "version": 2,
                "subnet_ip": self.subnet_calc_ip_edit.text().strip(),
                "subnet_prefix": self.subnet_calc_prefix_edit.text().strip(),
                "arp_subnet": self.arp_subnet_edit.text().strip(),
                "arp_timeout_ms": self.arp_timeout_edit.text().strip(),
                "arp_workers": self.arp_workers_edit.text().strip(),
                "oui_targets": self.oui_mac_edit.toPlainText().strip(),
            },
            "ping": {
                "targets": self.ping_targets_edit.toPlainText(),
                "count": self.ping_count_edit.text().strip(),
                "timeout_ms": self.ping_timeout_edit.text().strip(),
                "workers": self.ping_workers_edit.text().strip(),
                "continuous": self.ping_continuous_check.isChecked(),
            },
            "tcp": {
                "targets": self.tcp_targets_edit.toPlainText(),
                "ports": self.tcp_ports_edit.text().strip(),
                "count": self.tcp_count_edit.text().strip(),
                "timeout_ms": self.tcp_timeout_edit.text().strip(),
                "workers": self.tcp_workers_edit.text().strip(),
                "continuous": self.tcp_continuous_check.isChecked(),
            },
            "dns": {
                "query": self.dns_query_edit.text().strip(),
                "record_type": self.dns_type_combo.currentData()[0],
                "server": self.dns_server_edit.text().strip(),
            },
            "trace": {
                "target": self.trace_target_edit.text().strip(),
                "no_resolve": self.trace_no_resolve_check.isChecked(),
            },
            "ftp": self._build_ftp_tab_state() if hasattr(self, "_build_ftp_tab_state") else {
                "current_subtab": self.ftp_inner_tab.currentIndex(),
            },
            "iperf": {
                "mode": str(self.iperf_mode_combo.currentData() or ""),
                "use_public_server": self.iperf_use_public_server_check.isChecked(),
                "public_region": str(self.iperf_public_region_combo.currentData() or ""),
                "public_server_key": (
                    self._current_public_iperf_state_key()
                    if hasattr(self, "_current_public_iperf_state_key")
                    else str(self.iperf_public_server_combo.currentData() or "")
                ),
                "server": self.iperf_server_edit.text().strip(),
                "port": self.iperf_port_edit.text().strip(),
                "streams": self.iperf_streams_edit.text().strip(),
                "duration": self.iperf_duration_edit.text().strip(),
                "reverse": self.iperf_reverse_check.isChecked(),
                "udp": self.iperf_udp_check.isChecked(),
                "ipv6": self.iperf_ipv6_check.isChecked(),
            },
        }

    def restore_ui_state(self, ui_state: dict | None) -> None:
        state = dict(ui_state or {})
        if not state:
            return

        tools_state = state.get("tools", {})
        if not isinstance(tools_state, dict):
            tools_state = {}
        tool_key = self._tool_key_from_saved_state(state, tools_state)
        self.select_diagnostic_tab(tool_key)

        self.subnet_calc_ip_edit.setText(str(tools_state.get("subnet_ip", "") or ""))
        self.subnet_calc_prefix_edit.setText(str(tools_state.get("subnet_prefix", "") or ""))
        self.arp_subnet_edit.setText(str(tools_state.get("arp_subnet", "") or ""))
        self.arp_timeout_edit.setText(str(tools_state.get("arp_timeout_ms", "") or ""))
        self.arp_workers_edit.setText(str(tools_state.get("arp_workers", "") or ""))
        self.oui_mac_edit.setPlainText(str(tools_state.get("oui_targets", tools_state.get("oui_mac", "")) or ""))
        self.calculate_subnet_from_tools_inputs()

        ping_state = state.get("ping", {})
        self.ping_targets_edit.setPlainText(str(ping_state.get("targets", "") or ""))
        self.ping_count_edit.setText(str(ping_state.get("count", self.ping_count_edit.text()) or ""))
        self.ping_timeout_edit.setText(str(ping_state.get("timeout_ms", self.ping_timeout_edit.text()) or ""))
        self.ping_workers_edit.setText(str(ping_state.get("workers", self.ping_workers_edit.text()) or ""))
        self.ping_continuous_check.setChecked(bool(ping_state.get("continuous", False)))

        tcp_state = state.get("tcp", {})
        self.tcp_targets_edit.setPlainText(str(tcp_state.get("targets", "") or ""))
        self.tcp_ports_edit.setText(str(tcp_state.get("ports", self.tcp_ports_edit.text()) or ""))
        self.tcp_count_edit.setText(str(tcp_state.get("count", self.tcp_count_edit.text()) or ""))
        self.tcp_timeout_edit.setText(str(tcp_state.get("timeout_ms", self.tcp_timeout_edit.text()) or ""))
        self.tcp_workers_edit.setText(str(tcp_state.get("workers", self.tcp_workers_edit.text()) or ""))
        self.tcp_continuous_check.setChecked(bool(tcp_state.get("continuous", False)))

        dns_state = state.get("dns", {})
        self.dns_query_edit.setText(str(dns_state.get("query", "") or ""))
        self.dns_server_edit.setText(str(dns_state.get("server", "") or ""))
        dns_type = str(dns_state.get("record_type", "") or "")
        if dns_type:
            for index in range(self.dns_type_combo.count()):
                value, _description = self.dns_type_combo.itemData(index)
                if value == dns_type:
                    self.dns_type_combo.setCurrentIndex(index)
                    break
        self._update_dns_type_hint()

        trace_state = state.get("trace", {})
        self.trace_target_edit.setText(str(trace_state.get("target", "") or ""))
        self.trace_no_resolve_check.setChecked(bool(trace_state.get("no_resolve", False)))

        ftp_state = state.get("ftp", {})
        scp_state = state.get("scp", {})
        if hasattr(self, "_restore_ftp_tab_state"):
            self._restore_ftp_tab_state(ftp_state, scp_state)
        else:
            ftp_subtab = int(ftp_state.get("current_subtab", 0) or 0)
            if "current_subtab" not in ftp_state:
                if isinstance(scp_state, dict) and "current_subtab" in scp_state:
                    ftp_subtab = 2 + int(scp_state.get("current_subtab", 0) or 0)
            if 0 <= ftp_subtab < self.ftp_inner_tab.count():
                self.ftp_inner_tab.setCurrentIndex(ftp_subtab)

        iperf_state = state.get("iperf", {})
        iperf_mode = str(iperf_state.get("mode", "") or "")
        if iperf_mode:
            index = self.iperf_mode_combo.findData(iperf_mode)
            if index >= 0:
                self.iperf_mode_combo.setCurrentIndex(index)
        self._preferred_public_iperf_region = str(iperf_state.get("public_region", "") or "")
        public_server_key = str(iperf_state.get("public_server_key", "") or "")
        self._preferred_public_iperf_key = public_server_key
        self.iperf_public_region_combo.blockSignals(True)
        self.iperf_public_server_combo.blockSignals(True)
        if hasattr(self, "_ensure_public_iperf_state_placeholders"):
            self._ensure_public_iperf_state_placeholders(self._preferred_public_iperf_region, public_server_key)
        if self._preferred_public_iperf_region:
            region_index = self.iperf_public_region_combo.findData(self._preferred_public_iperf_region)
            if region_index >= 0:
                self.iperf_public_region_combo.setCurrentIndex(region_index)
        if public_server_key:
            index = self.iperf_public_server_combo.findData(public_server_key)
            if index >= 0:
                self.iperf_public_server_combo.setCurrentIndex(index)
        self.iperf_public_server_combo.blockSignals(False)
        self.iperf_public_region_combo.blockSignals(False)
        self.iperf_use_public_server_check.setChecked(bool(iperf_state.get("use_public_server", False)))
        self.iperf_server_edit.setText(str(iperf_state.get("server", "") or ""))
        self.iperf_port_edit.setText(str(iperf_state.get("port", self.iperf_port_edit.text()) or ""))
        self.iperf_streams_edit.setText(str(iperf_state.get("streams", self.iperf_streams_edit.text()) or ""))
        self.iperf_duration_edit.setText(str(iperf_state.get("duration", self.iperf_duration_edit.text()) or ""))
        self.iperf_reverse_check.setChecked(bool(iperf_state.get("reverse", False)))
        self.iperf_udp_check.setChecked(bool(iperf_state.get("udp", False)))
        self.iperf_ipv6_check.setChecked(bool(iperf_state.get("ipv6", False)))
        self._sync_public_iperf_target(overwrite_port=not bool(self.iperf_port_edit.text().strip()))
        self._update_iperf_mode_state()

    def _tool_key_from_saved_state(self, state: dict, tools_state: dict) -> str:
        current_tool_key = str(state.get("current_tool_key", "") or "")
        if current_tool_key in self._diagnostic_tool_index_by_key:
            return current_tool_key

        legacy_tools_map = ["commands", "arp", "subnet", "oui"]
        legacy_tab_map = {
            1: "ping",
            2: "tcp",
            3: "dns",
            4: "trace",
            5: "transfer",
            6: "iperf",
            7: "transfer",
        }
        if "current_tab" not in state:
            return "ping"
        try:
            current_tab = int(state.get("current_tab", 0) or 0)
        except (TypeError, ValueError):
            current_tab = 0
        if current_tab == 0:
            try:
                tools_version = int(tools_state.get("version", 1) or 1)
                tools_subtab = int(tools_state.get("current_subtab", 0) or 0)
            except (TypeError, ValueError):
                tools_version = 1
                tools_subtab = 0
            if tools_version < 2 and tools_subtab >= 2:
                tools_subtab += 1
            if 0 <= tools_subtab < len(legacy_tools_map):
                return legacy_tools_map[tools_subtab]
            return "commands"
        return legacy_tab_map.get(current_tab, "ping")

    def _start_worker(
        self,
        fn: Callable,
        *args,
        on_started: Callable[[], None] | None = None,
        on_result: Callable | None = None,
        on_progress: Callable | None = None,
        on_finished: Callable | None = None,
        on_error: Callable[[str], None] | None = None,
        error_title: str = "작업 실패",
        **kwargs,
    ) -> None:
        if self._shutting_down:
            return
        self._job_runner.start(
            fn,
            *args,
            on_started=on_started,
            on_progress=on_progress,
            on_result=on_result,
            on_finished=on_finished,
            on_error=on_error,
            error_title=error_title,
            **kwargs,
        )

    def _discard_worker(self, worker) -> None:
        self._job_runner._discard_worker(worker)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        event_names = (
            "ftp_server_cancel_event",
            "scp_server_cancel_event",
            "tftp_server_cancel_event",
            "ftp_client_cancel_event",
            "scp_client_cancel_event",
            "tftp_client_cancel_event",
            "ping_cancel_event",
            "tcp_cancel_event",
            "trace_cancel_event",
            "arp_cancel_event",
            "iperf_cancel_event",
            "iperf_manage_cancel_event",
        )
        for name in event_names:
            cancel_event = getattr(self, name, None)
            if cancel_event is not None:
                cancel_event.set()
