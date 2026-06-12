from __future__ import annotations

import csv
import re
from datetime import datetime
from threading import Event
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
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
from app.utils.validators import parse_positive_int


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
        layout.addWidget(make_step_hint("작업 흐름: 도구 선택 → 대상 입력 → 실행 → 결과 저장"), 0)

        self.diagnostic_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.diagnostic_splitter.setChildrenCollapsible(False)
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
            ("transfer", "파일 전송", self._build_ftp_tab),
            ("commands", "명령 출력", self._build_command_tools_page),
        ]
        for key, label, builder in tool_pages:
            self._add_diagnostic_tool(key, builder(), label)
        self._refresh_oui_status_labels()

        self.diagnostic_splitter.addWidget(self.diagnostic_tool_list)
        self.diagnostic_splitter.addWidget(self.diagnostic_stack)
        self.diagnostic_splitter.setStretchFactor(0, 0)
        self.diagnostic_splitter.setStretchFactor(1, 1)
        self.diagnostic_splitter.setSizes([220, 1060])
        layout.addWidget(self.diagnostic_splitter, 1)

        self.diagnostic_tool_list.currentRowChanged.connect(self._handle_tool_changed)
        self.diagnostic_stack.currentChanged.connect(self._sync_tool_list_to_stack)
        self.diagnostic_tool_list.setCurrentRow(0)

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
