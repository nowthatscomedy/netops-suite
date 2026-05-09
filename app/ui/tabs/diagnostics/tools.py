from __future__ import annotations

import re
from threading import Event
from typing import Callable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.network_models import ArpScanEntry
from app.models.result_models import OperationResult
from app.utils.validators import ValidationError, calculate_subnet_details


class ToolsDiagnosticsMixin:
    def _build_tools_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tools_inner_tab = QTabWidget()
        self.tools_inner_tab.addTab(self._build_command_tools_page(), "명령 출력")
        self.tools_inner_tab.addTab(self._build_arp_scan_page(), "ARP 스캔")
        self.tools_inner_tab.addTab(self._build_subnet_calc_page(), "서브넷 계산기")
        self.tools_inner_tab.addTab(self._build_oui_lookup_page(), "MAC OUI")
        layout.addWidget(self.tools_inner_tab, 1)
        self._refresh_oui_status_labels()
        return page

    def _build_command_tools_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        button_row = QHBoxLayout()
        self.public_ip_button = QPushButton("공인 IP 확인")
        self.snapshot_button = QPushButton("현재 인터페이스")
        self.ipconfig_button = QPushButton("ipconfig /all")
        self.route_button = QPushButton("route print")
        self.arp_button = QPushButton("arp -a")
        self.flush_dns_button = QPushButton("DNS 캐시 비우기")
        for button in (
            self.public_ip_button,
            self.snapshot_button,
            self.ipconfig_button,
            self.route_button,
            self.arp_button,
            self.flush_dns_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.tools_output = self._output()
        layout.addWidget(self.tools_output, 1)

        self.snapshot_button.clicked.connect(self.load_interface_snapshot)
        self.ipconfig_button.clicked.connect(lambda: self._run_tools_command(self.state.trace_service.run_ipconfig_all))
        self.route_button.clicked.connect(lambda: self._run_tools_command(self.state.trace_service.run_route_print))
        self.arp_button.clicked.connect(lambda: self._run_tools_command(self.state.trace_service.run_arp_table))
        self.flush_dns_button.clicked.connect(
            lambda: self._run_tools_command(self.state.dns_service.flush_dns_cache)
        )
        self.public_ip_button.clicked.connect(self.check_public_ip)
        return page

    def _build_subnet_calc_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        input_group = QGroupBox("입력")
        input_layout = QVBoxLayout(input_group)
        subnet_form = QFormLayout()
        self.subnet_calc_ip_edit = QLineEdit()
        self.subnet_calc_ip_edit.setPlaceholderText("예: 192.168.0.10")
        self.subnet_calc_prefix_edit = QLineEdit()
        self.subnet_calc_prefix_edit.setPlaceholderText("예: 24 또는 255.255.255.0")
        self.subnet_calc_interface_combo = QComboBox()
        self.subnet_calc_interface_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.subnet_calc_refresh_button = QPushButton("인터페이스 불러오기")
        self.subnet_calc_use_selected_button = QPushButton("선택 값 자동 입력")
        subnet_form.addRow("IPv4", self.subnet_calc_ip_edit)
        subnet_form.addRow("Prefix / Mask", self.subnet_calc_prefix_edit)

        interface_row = QHBoxLayout()
        interface_row.addWidget(self.subnet_calc_interface_combo, 1)
        interface_row.addWidget(self.subnet_calc_refresh_button)
        interface_row.addWidget(self.subnet_calc_use_selected_button)
        subnet_form.addRow("인터페이스", interface_row)
        input_layout.addLayout(subnet_form)

        subnet_button_row = QHBoxLayout()
        self.subnet_calc_button = QPushButton("계산")
        subnet_button_row.addWidget(self.subnet_calc_button)
        subnet_button_row.addStretch(1)
        input_layout.addLayout(subnet_button_row)

        self.subnet_calc_status_label = QLabel("IPv4와 Prefix를 입력하면 서브넷 정보를 계산합니다.")
        self.subnet_calc_status_label.setWordWrap(True)
        self.subnet_calc_status_label.setStyleSheet("color:#666;")
        input_layout.addWidget(self.subnet_calc_status_label)
        layout.addWidget(input_group)

        result_group = QGroupBox("계산 결과")
        result_layout = QVBoxLayout(result_group)
        summary_grid = QGridLayout()
        self.subnet_calc_summary_labels: dict[str, QLabel] = {}
        cards = [
            ("network_address", "네트워크 주소", "#1b5e20"),
            ("host_range", "사용 가능 범위", "#1565c0"),
            ("broadcast_address", "브로드캐스트", "#ef6c00"),
            ("usable_hosts", "사용 가능 호스트", "#6a1b9a"),
        ]
        for index, (key, title, color) in enumerate(cards):
            card, value_label = self._build_subnet_metric_card(title, color)
            self.subnet_calc_summary_labels[key] = value_label
            summary_grid.addWidget(card, index // 2, index % 2)
        result_layout.addLayout(summary_grid)

        self.subnet_calc_result_hint = QLabel("계산 후 요약 카드와 상세 네트워크 정보를 아래에서 확인할 수 있습니다.")
        self.subnet_calc_result_hint.setStyleSheet("color:#666; padding:4px 2px 2px 2px;")
        result_layout.addWidget(self.subnet_calc_result_hint)

        self.subnet_calc_detail_table = QTableWidget(0, 2)
        self.subnet_calc_detail_table.setHorizontalHeaderLabels(["항목", "값"])
        self.subnet_calc_detail_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.subnet_calc_detail_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.subnet_calc_detail_table.verticalHeader().setVisible(False)
        self.subnet_calc_detail_table.horizontalHeader().setStretchLastSection(True)
        self.subnet_calc_detail_table.setAlternatingRowColors(True)
        self.subnet_calc_detail_table.setColumnWidth(0, 180)
        self.subnet_calc_detail_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        result_layout.addWidget(self.subnet_calc_detail_table, 1)
        self._clear_subnet_calc_results()
        layout.addWidget(result_group, 1)

        self.subnet_calc_button.clicked.connect(self.calculate_subnet_from_tools_inputs)
        self.subnet_calc_refresh_button.clicked.connect(self.refresh_subnet_calc_interfaces)
        self.subnet_calc_use_selected_button.clicked.connect(self.use_selected_subnet_calc_interface)
        self.subnet_calc_ip_edit.returnPressed.connect(self.calculate_subnet_from_tools_inputs)
        self.subnet_calc_prefix_edit.returnPressed.connect(self.calculate_subnet_from_tools_inputs)
        return page

    def _build_arp_scan_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("ARP 스캔 / 같은 대역 장비 탐색")
        form = QFormLayout(group)
        self.arp_subnet_edit = QLineEdit()
        self.arp_subnet_edit.setPlaceholderText("예: 192.168.0.0/24")
        self.arp_subnet_combo = QComboBox()
        self.arp_subnet_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.arp_refresh_subnets_button = QPushButton("인터페이스 불러오기")
        self.arp_use_selected_subnet_button = QPushButton("선택 값 자동 입력")
        self.arp_timeout_edit = QLineEdit()
        self.arp_timeout_edit.setPlaceholderText("800")
        self.arp_workers_edit = QLineEdit()
        self.arp_workers_edit.setPlaceholderText("64")
        self.arp_workers_edit.setToolTip(
            "ARP 스캔은 각 대상에 동시에 Ping을 보내는 방식입니다. 값이 높을수록 빨라지지만 부하도 커집니다."
        )
        self.arp_start_button = QPushButton("스캔")
        self.arp_cancel_button = QPushButton("중지")
        self.arp_cancel_button.setEnabled(False)
        self.arp_refresh_oui_button = QPushButton("OUI 캐시 갱신")
        self.arp_oui_status_label = QLabel()
        self.arp_oui_status_label.setStyleSheet("color:#666;")

        subnet_button_row = QHBoxLayout()
        subnet_button_row.addWidget(self.arp_subnet_combo, 1)
        subnet_button_row.addWidget(self.arp_refresh_subnets_button)
        subnet_button_row.addWidget(self.arp_use_selected_subnet_button)

        action_row = QHBoxLayout()
        action_row.addWidget(self.arp_start_button)
        action_row.addWidget(self.arp_cancel_button)
        action_row.addSpacing(8)
        action_row.addWidget(self.arp_refresh_oui_button)
        action_row.addStretch(1)

        form.addRow("서브넷", self.arp_subnet_edit)
        form.addRow("인터페이스", subnet_button_row)
        form.addRow("Timeout (ms)", self.arp_timeout_edit)
        form.addRow("동시 실행 수", self.arp_workers_edit)
        form.addRow("", action_row)
        form.addRow("OUI 캐시", self.arp_oui_status_label)
        layout.addWidget(group)

        self.arp_table = QTableWidget(0, 8)
        self.arp_table.setHorizontalHeaderLabels(
            ["IP", "MAC", "벤더", "상태", "감지", "응답(ms)", "ARP 타입", "인터페이스"]
        )
        self._setup_table(self.arp_table)
        self._set_stretch_columns(self.arp_table, 2, 7)
        layout.addWidget(self.arp_table, 1)

        self.arp_output = self._output()
        self.arp_output.setMaximumHeight(150)
        layout.addWidget(self.arp_output)

        self.arp_refresh_subnets_button.clicked.connect(self.refresh_arp_subnets)
        self.arp_use_selected_subnet_button.clicked.connect(self.use_selected_arp_subnet)
        self.arp_start_button.clicked.connect(self.start_arp_scan)
        self.arp_cancel_button.clicked.connect(self.cancel_arp_scan)
        self.arp_refresh_oui_button.clicked.connect(lambda: self.refresh_oui_cache(self.arp_output))
        return page

    def _build_oui_lookup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("MAC OUI 벤더 조회")
        form = QFormLayout(group)
        self.oui_mac_edit = QPlainTextEdit()
        self.oui_mac_edit.setMaximumHeight(110)
        self.oui_mac_edit.setPlaceholderText(
            "예:\n"
            "AP-1,58:86:94:A1:5A:BA\n"
            "BSSID: 88-36-6C-8A-E1-D4\n"
            "0011.2233.4455\n"
            "58 86 94 A1 5A BA"
        )
        self.oui_lookup_button = QPushButton("조회")
        self.oui_refresh_button = QPushButton("OUI 캐시 갱신")
        self.oui_status_label = QLabel()
        self.oui_status_label.setStyleSheet("color:#666;")

        action_row = QHBoxLayout()
        action_row.addWidget(self.oui_lookup_button)
        action_row.addWidget(self.oui_refresh_button)
        action_row.addStretch(1)

        form.addRow("MAC 주소 목록", self.oui_mac_edit)
        form.addRow("", action_row)
        form.addRow("캐시 상태", self.oui_status_label)
        layout.addWidget(group)

        self.oui_table = QTableWidget(0, 5)
        self.oui_table.setHorizontalHeaderLabels(["이름", "입력 MAC", "정규화", "벤더", "상태"])
        self._setup_table(self.oui_table)
        header = self.oui_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.Stretch)
        header.setSectionResizeMode(4, header.ResizeMode.ResizeToContents)
        layout.addWidget(self.oui_table, 1)

        self.oui_result_output = self._output()
        self.oui_result_output.setMaximumHeight(160)
        layout.addWidget(self.oui_result_output)

        self.oui_lookup_button.clicked.connect(self.lookup_oui_vendor)
        self.oui_refresh_button.clicked.connect(lambda: self.refresh_oui_cache(self.oui_result_output))
        return page

    def load_interface_snapshot(self) -> None:
        self.tools_output.setPlainText("현재 인터페이스 정보를 불러오는 중입니다...")
        self._start_worker(
            self.state.network_interface_service.list_adapters,
            on_result=lambda adapters: self.tools_output.setPlainText(
                self.state.network_interface_service.format_adapter_snapshot(adapters)
            ),
            error_title="인터페이스 정보 조회 실패",
        )

    def _run_tools_command(self, fn: Callable) -> None:
        self.tools_output.setPlainText("명령 실행 중입니다...")
        self._start_worker(
            fn,
            on_result=lambda result: self.tools_output.setPlainText(result.details or result.message),
            error_title="도구 실행 실패",
        )

    def check_public_ip(self) -> None:
        self.tools_output.setPlainText("공인 IP를 확인하는 중입니다...")
        self._start_worker(
            self.state.public_ip_service.check_public_ip,
            on_result=self._show_public_ip_result,
            error_title="공인 IP 확인 실패",
        )

    def _show_public_ip_result(self, result: OperationResult) -> None:
        self.tools_output.setPlainText(result.details or result.message)
        if not result.success:
            QMessageBox.warning(self, "공인 IP 확인 실패", result.details or result.message)

    def calculate_subnet_from_tools_inputs(self) -> None:
        ip_text = self.subnet_calc_ip_edit.text().strip()
        prefix_text = self.subnet_calc_prefix_edit.text().strip()

        if not ip_text and not prefix_text:
            self.subnet_calc_status_label.setText("IPv4와 Prefix를 입력하면 서브넷 정보를 계산합니다.")
            self.subnet_calc_status_label.setStyleSheet("color:#666;")
            self._clear_subnet_calc_results()
            return

        try:
            details = calculate_subnet_details(ip_text, prefix_text)
        except ValidationError as exc:
            self.subnet_calc_status_label.setText(str(exc))
            self.subnet_calc_status_label.setStyleSheet("color:#b71c1c;")
            self._clear_subnet_calc_results()
            return

        self.subnet_calc_status_label.setText(
            f"계산 완료: {details['address_scope']} | 네트워크 {details['network_address']} | 사용 가능 호스트 {details['usable_hosts']}"
        )
        self.subnet_calc_status_label.setStyleSheet("color:#1b5e20;")
        self._populate_subnet_calc_results(details)

    def refresh_subnet_calc_interfaces(self) -> None:
        self.subnet_calc_status_label.setText("인터페이스 목록을 불러오는 중입니다...")
        self.subnet_calc_status_label.setStyleSheet("color:#666;")
        self._start_worker(
            self.state.network_interface_service.list_adapters,
            on_result=self._populate_subnet_calc_interfaces,
            error_title="인터페이스 목록 조회 실패",
        )

    def _populate_subnet_calc_interfaces(self, adapters) -> None:
        current_ip = ""
        current_prefix = ""
        current_data = self.subnet_calc_interface_combo.currentData()
        if isinstance(current_data, dict):
            current_ip = str(current_data.get("ip", "") or "")
            current_prefix = str(current_data.get("prefix", "") or "")

        self.subnet_calc_interface_combo.clear()
        candidates: list[dict[str, str]] = []
        for adapter in adapters:
            if not adapter.ipv4 or not adapter.prefix_length:
                continue
            if adapter.ipv4.startswith("127.") or adapter.ipv4.startswith("169.254."):
                continue

            description = (adapter.interface_description or "").strip()
            if description and description.lower() != adapter.name.strip().lower():
                interface_text = f"{adapter.name} ({description})"
            else:
                interface_text = adapter.name

            subnet_text = f"{adapter.ipv4}/{adapter.prefix_length}"
            payload = {
                "ip": adapter.ipv4,
                "prefix": str(adapter.prefix_length),
                "subnet": subnet_text,
            }
            candidates.append(payload)
            self.subnet_calc_interface_combo.addItem(f"{interface_text} - {subnet_text}", payload)

        if current_ip and current_prefix:
            for index in range(self.subnet_calc_interface_combo.count()):
                data = self.subnet_calc_interface_combo.itemData(index)
                if isinstance(data, dict) and data.get("ip") == current_ip and str(data.get("prefix")) == current_prefix:
                    self.subnet_calc_interface_combo.setCurrentIndex(index)
                    break

        if candidates:
            self.subnet_calc_status_label.setText(f"사용 가능한 인터페이스 {len(candidates)}개를 찾았습니다.")
            self.subnet_calc_status_label.setStyleSheet("color:#666;")
        else:
            self.subnet_calc_status_label.setText("사용 가능한 인터페이스를 찾지 못했습니다.")
            self.subnet_calc_status_label.setStyleSheet("color:#b71c1c;")
            self._clear_subnet_calc_results()

    def use_selected_subnet_calc_interface(self) -> None:
        data = self.subnet_calc_interface_combo.currentData()
        if not isinstance(data, dict):
            QMessageBox.warning(self, "선택 필요", "먼저 인터페이스 목록을 불러오고 선택해 주세요.")
            return

        self.subnet_calc_ip_edit.setText(str(data.get("ip", "") or ""))
        self.subnet_calc_prefix_edit.setText(str(data.get("prefix", "") or ""))
        self.calculate_subnet_from_tools_inputs()

    def refresh_arp_subnets(self) -> None:
        self.arp_output.setPlainText("인터페이스 목록과 서브넷 정보를 불러오는 중입니다...")
        self._start_worker(
            self.state.network_interface_service.list_adapters,
            on_result=self._populate_arp_subnets,
            error_title="인터페이스 목록 조회 실패",
        )

    def _populate_arp_subnets(self, adapters) -> None:
        self._populate_subnet_calc_interfaces(adapters)
        current_subnet = str(self.arp_subnet_combo.currentData() or "")
        candidates = self.state.arp_scan_service.list_candidate_subnets(adapters)
        self.arp_subnet_combo.clear()
        self.arp_subnet_candidates = [subnet for _label, subnet in candidates]
        for label, subnet in candidates:
            self.arp_subnet_combo.addItem(label, subnet)

        if current_subnet:
            index = self.arp_subnet_combo.findData(current_subnet)
            if index >= 0:
                self.arp_subnet_combo.setCurrentIndex(index)

        if candidates:
            self.arp_output.setPlainText(f"사용 가능한 인터페이스 서브넷 {len(candidates)}개를 찾았습니다.")
        else:
            self.arp_output.setPlainText("사용 가능한 인터페이스 서브넷을 찾지 못했습니다.")

    def use_selected_arp_subnet(self) -> None:
        subnet = str(self.arp_subnet_combo.currentData() or "").strip()
        if not subnet:
            QMessageBox.warning(self, "선택 필요", "먼저 인터페이스 목록을 불러오고 선택해 주세요.")
            return
        self.arp_subnet_edit.setText(subnet)

    def start_arp_scan(self) -> None:
        try:
            timeout_ms = self._positive_int_or_default(self.arp_timeout_edit, "ARP Timeout", 800)
            workers = self._positive_int_or_default(self.arp_workers_edit, "ARP 동시 실행 수", 64)
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        self.arp_table.setRowCount(0)
        self.arp_output.clear()
        self.arp_cancel_event = Event()
        self._current_arp_scan_subnet = self.arp_subnet_edit.text().strip()
        self._set_arp_running(True)
        self._start_worker(
            self.state.arp_scan_service.run_scan,
            self._current_arp_scan_subnet,
            timeout_ms,
            workers,
            cancel_event=self.arp_cancel_event,
            on_progress=self.arp_output.appendPlainText,
            on_result=self._finish_arp_scan,
            on_finished=lambda: self._set_arp_running(False),
            error_title="ARP 스캔 실패",
        )

    def _finish_arp_scan(self, result: OperationResult) -> None:
        entries = result.payload if isinstance(result.payload, list) else []
        detection_map, detection_lines = self._analyze_arp_entries(entries, self._current_arp_scan_subnet)
        self._populate_arp_table(entries, detection_map)
        if self.arp_output.toPlainText().strip():
            self.arp_output.appendPlainText("")
            self.arp_output.appendPlainText(f"[결과] {result.message}")
        else:
            self.arp_output.setPlainText(result.message)
        if detection_lines and (entries or result.success):
            self.arp_output.appendPlainText("")
            for line in detection_lines:
                self.arp_output.appendPlainText(line)

    def _populate_arp_table(self, entries: list[ArpScanEntry], detection_map: dict[str, tuple[str, QColor | None]]) -> None:
        self.arp_table.setRowCount(0)
        for entry in entries:
            row = self.arp_table.rowCount()
            self.arp_table.insertRow(row)
            detection_text, detection_color = detection_map.get(entry.ip_address, ("정상", None))
            values = [
                entry.ip_address,
                entry.mac_address or "-",
                entry.vendor or "-",
                entry.status_text,
                detection_text,
                f"{entry.response_ms:.0f}" if entry.response_ms is not None else "-",
                entry.arp_type or "-",
                entry.interface_name or "-",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    if entry.reachable:
                        item.setForeground(QColor("#1b5e20"))
                    elif entry.mac_address:
                        item.setForeground(QColor("#ef6c00"))
                    else:
                        item.setForeground(QColor("#b71c1c"))
                elif column == 4 and detection_color is not None:
                    item.setForeground(detection_color)
                self.arp_table.setItem(row, column, item)

    def _analyze_arp_entries(
        self,
        entries: list[ArpScanEntry],
        subnet_text: str,
    ) -> tuple[dict[str, tuple[str, QColor | None]], list[str]]:
        subnet_key = subnet_text.strip()
        previous_map = self._arp_scan_history.get(subnet_key, {})
        current_map: dict[str, str] = {}
        mac_to_ips: dict[str, list[str]] = {}

        for entry in entries:
            normalized_mac = self._normalize_mac(entry.mac_address)
            if not normalized_mac:
                continue
            current_map[entry.ip_address] = normalized_mac
            mac_to_ips.setdefault(normalized_mac, []).append(entry.ip_address)

        duplicate_mac_map = {
            mac: sorted(ips, key=self._ip_sort_key)
            for mac, ips in mac_to_ips.items()
            if len(ips) > 1
        }

        changed_entries: list[tuple[str, str, str]] = []
        detection_map: dict[str, tuple[str, QColor | None]] = {}
        for entry in entries:
            normalized_mac = self._normalize_mac(entry.mac_address)
            labels: list[str] = []
            color: QColor | None = None

            previous_mac = previous_map.get(entry.ip_address, "")
            if normalized_mac and previous_mac and previous_mac != normalized_mac:
                labels.append("IP 충돌 의심")
                color = QColor("#b71c1c")
                changed_entries.append(
                    (entry.ip_address, self._format_mac(previous_mac), self._format_mac(normalized_mac))
                )

            duplicate_ips = duplicate_mac_map.get(normalized_mac, [])
            if duplicate_ips:
                labels.append(f"중복 MAC ({len(duplicate_ips)} IP)")
                if color is None:
                    color = QColor("#ef6c00")

            detection_map[entry.ip_address] = (" / ".join(labels) if labels else "정상", color)

        if subnet_key and current_map:
            self._arp_scan_history[subnet_key] = current_map

        output_lines: list[str] = []
        if duplicate_mac_map:
            output_lines.append(f"[감지] 중복 MAC {len(duplicate_mac_map)}건")
            for mac, ips in sorted(duplicate_mac_map.items(), key=lambda item: self._ip_sort_key(item[1][0])):
                output_lines.append(f"  - {self._format_mac(mac)} -> {', '.join(ips)}")
        if changed_entries:
            output_lines.append(f"[감지] IP 충돌 의심 {len(changed_entries)}건")
            for ip_address, previous_mac, current_mac in sorted(changed_entries, key=lambda item: self._ip_sort_key(item[0])):
                output_lines.append(f"  - {ip_address}: {previous_mac} -> {current_mac}")
        if not output_lines:
            output_lines.append("[감지] 특이사항 없음")
        return detection_map, output_lines

    def _normalize_mac(self, mac_address: str) -> str:
        return re.sub(r"[^0-9A-Fa-f]", "", mac_address or "").upper()

    def _format_mac(self, normalized_mac: str) -> str:
        if len(normalized_mac) != 12:
            return normalized_mac or "-"
        return "-".join(normalized_mac[index : index + 2] for index in range(0, 12, 2))

    def _ip_sort_key(self, ip_address: str) -> tuple[int, ...]:
        parts = [int(part) for part in ip_address.split(".") if part.isdigit()]
        return tuple(parts) if len(parts) == 4 else (999, 999, 999, 999)

    def _set_arp_running(self, running: bool) -> None:
        self.arp_start_button.setEnabled(not running)
        self.arp_cancel_button.setEnabled(running)
        self.arp_refresh_subnets_button.setEnabled(not running)
        self.arp_use_selected_subnet_button.setEnabled(not running)
        self.arp_refresh_oui_button.setEnabled(not running)

    def cancel_arp_scan(self) -> None:
        if self.arp_cancel_event:
            self.arp_cancel_event.set()

    def lookup_oui_vendor(self) -> None:
        raw_text = self.oui_mac_edit.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "입력 확인", "조회할 MAC 주소를 한 줄에 하나씩 입력해 주세요.")
            return

        entries = [
            self.state.oui_service.split_label_and_mac(line)
            for line in raw_text.splitlines()
            if line.strip()
        ]
        if not entries:
            QMessageBox.warning(self, "입력 확인", "조회할 MAC 주소를 한 줄에 하나씩 입력해 주세요.")
            return

        self.oui_table.setRowCount(0)
        found_count = 0
        invalid_count = 0
        lines = [f"OUI 조회 {len(entries)}건", ""]

        for index, (name, mac_address) in enumerate(entries, start=1):
            normalized = self.state.oui_service.normalize_mac(mac_address)
            match = self.state.oui_service.lookup(mac_address) if normalized else None

            if not normalized:
                judgment = "입력 형식 확인 필요"
                vendor = "-"
                invalid_count += 1
            elif match is None:
                judgment = "벤더 정보 없음"
                vendor = "-"
            else:
                judgment = "벤더 확인됨"
                vendor = match.organization
                found_count += 1

            row = self.oui_table.rowCount()
            self.oui_table.insertRow(row)
            values = [name if name != mac_address else "-", mac_address, normalized or "-", vendor, judgment]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    if judgment == "벤더 확인됨":
                        item.setForeground(QColor("#1b5e20"))
                    elif judgment == "입력 형식 확인 필요":
                        item.setForeground(QColor("#b71c1c"))
                    else:
                        item.setForeground(QColor("#ef6c00"))
                self.oui_table.setItem(row, column, item)

            summary_name = name if name != mac_address else f"항목 {index}"
            lines.append(f"[{index}] {summary_name}: {mac_address} -> {vendor if vendor != '-' else judgment}")

        mismatch_count = len(entries) - found_count - invalid_count
        lines.extend(
            [
                "",
                f"일치 {found_count}건 | 미일치 {mismatch_count}건 | 입력 오류 {invalid_count}건",
                self.state.oui_service.cache_summary(),
            ]
        )
        self.oui_result_output.setPlainText("\n".join(lines))

    def refresh_oui_cache(self, output_widget: QPlainTextEdit) -> None:
        output_widget.clear()
        self._start_worker(
            self.state.oui_service.refresh_cache,
            on_progress=output_widget.appendPlainText,
            on_result=lambda result, widget=output_widget: self._finish_oui_refresh(result, widget),
            error_title="OUI 캐시 갱신 실패",
        )

    def _finish_oui_refresh(self, result: OperationResult, output_widget: QPlainTextEdit) -> None:
        self._refresh_oui_status_labels()
        if output_widget.toPlainText().strip():
            output_widget.appendPlainText("")
            output_widget.appendPlainText(f"[결과] {result.message}")
            if result.details:
                output_widget.appendPlainText(result.details)
        else:
            output_widget.setPlainText(result.message + ("\n\n" + result.details if result.details else ""))

    def _refresh_oui_status_labels(self) -> None:
        summary = self.state.oui_service.cache_summary()
        if hasattr(self, "arp_oui_status_label"):
            self.arp_oui_status_label.setText(summary)
        if hasattr(self, "oui_status_label"):
            self.oui_status_label.setText(summary)
