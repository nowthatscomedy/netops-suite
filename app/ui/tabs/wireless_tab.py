from __future__ import annotations

from datetime import datetime
import re
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.models.network_models import NearbyAccessPoint, WirelessInfo
from app.ui.common import sortable_table_item
from app.utils.parser import summarize_channels
from app.utils.threading_utils import FunctionWorker


class WirelessTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._active_workers: list[FunctionWorker] = []
        self._wireless_refresh_running = False
        self._nearby_refresh_running = False
        self._startup_refresh_requested = False
        self.current_info: WirelessInfo | None = None
        self.previous_info: WirelessInfo | None = None
        self.nearby_access_points: list[NearbyAccessPoint] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._handle_auto_refresh_tick)
        self.nearby_timer = QTimer(self)
        self.nearby_timer.timeout.connect(self._handle_nearby_auto_refresh_tick)

        self._build_ui()
        QTimer.singleShot(0, self._rebuild_status_grid)

    def start_initial_refresh(self) -> None:
        if self._startup_refresh_requested:
            return
        self._startup_refresh_requested = True
        self.refresh_wireless_info()
        self.refresh_nearby_access_points_quietly()
        if self.auto_refresh_check.isChecked():
            self.timer.start(self.interval_spin.value() * 1000)
        if self.nearby_auto_refresh_check.isChecked():
            self.nearby_timer.start(self.nearby_interval_spin.value() * 1000)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        layout.addLayout(top_row, 1)

        self.status_group = QGroupBox("현재 Wi-Fi 상태")
        status_layout = QVBoxLayout(self.status_group)
        controls = QHBoxLayout()
        self.refresh_button = QPushButton("새로고침")
        self.auto_refresh_check = QCheckBox("자동 갱신")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 30)
        self.interval_spin.setValue(int(self.state.app_config.get("wireless_refresh_interval_sec", 2)))
        self.interval_spin.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.auto_refresh_check)
        controls.addWidget(QLabel("주기(초)"))
        controls.addWidget(self.interval_spin)
        controls.addStretch(1)
        status_layout.addLayout(controls)

        self.info_fields = [
            ("interface_name", "어댑터"),
            ("state", "상태"),
            ("ssid", "SSID"),
            ("bssid", "BSSID"),
            ("signal", "신호"),
            ("channel", "채널"),
            ("band", "대역"),
            ("radio_type", "무선 규격"),
            ("receive_rate", "수신 속도"),
            ("transmit_rate", "송신 속도"),
        ]
        self.info_labels: dict[str, QLabel] = {}
        self.status_cards: dict[str, QWidget] = {}
        self.status_grid = QGridLayout()
        self.status_grid.setContentsMargins(0, 0, 0, 0)
        self.status_grid.setHorizontalSpacing(10)
        self.status_grid.setVerticalSpacing(10)

        for key, title in self.info_fields:
            card = QWidget()
            card.setObjectName("wirelessStatusCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(4)

            title_label = QLabel(title)
            title_label.setStyleSheet("color:#666; font-size:11px; font-weight:600;")

            value_label = QLabel("-")
            value_label.setWordWrap(True)
            value_label.setStyleSheet("font-size:13px; font-weight:600;")

            card_layout.addWidget(title_label)
            card_layout.addWidget(value_label)

            self.info_labels[key] = value_label
            self.status_cards[key] = card

        self.status_group.setStyleSheet(
            "#wirelessStatusCard { background:#fafafa; border:1px solid #dcdcdc; border-radius:6px; }"
        )
        status_layout.addLayout(self.status_grid)
        top_row.addWidget(self.status_group, 2)

        change_group = QGroupBox("연결 변화 로그")
        change_layout = QVBoxLayout(change_group)
        self.change_log = QListWidget()
        change_layout.addWidget(self.change_log)
        top_row.addWidget(change_group, 1)

        nearby_group = QGroupBox("주변 AP / 채널 현황")
        nearby_layout = QVBoxLayout(nearby_group)

        nearby_controls = QHBoxLayout()
        self.nearby_refresh_button = QPushButton("주변 AP 새로고침")
        self.nearby_refresh_oui_button = QPushButton("OUI 캐시 갱신")
        self.nearby_summary_label = QLabel("스캔 전")
        self.nearby_auto_refresh_check = QCheckBox("자동 갱신")
        self.nearby_interval_spin = QSpinBox()
        self.nearby_interval_spin.setRange(5, 300)
        self.nearby_interval_spin.setValue(int(self.state.app_config.get("wireless_nearby_refresh_interval_sec", 30)))
        self.nearby_interval_spin.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        self.nearby_summary_label.setWordWrap(True)
        self.nearby_summary_label.setMinimumWidth(0)
        self.nearby_summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.nearby_summary_label.setStyleSheet("color:#666;")
        nearby_controls.addWidget(self.nearby_refresh_button)
        nearby_controls.addWidget(self.nearby_refresh_oui_button)
        nearby_controls.addSpacing(8)
        nearby_controls.addWidget(self.nearby_auto_refresh_check)
        nearby_controls.addWidget(QLabel("주기(초)"))
        nearby_controls.addWidget(self.nearby_interval_spin)
        nearby_controls.addStretch(1)
        nearby_layout.addLayout(nearby_controls)
        nearby_layout.addWidget(self.nearby_summary_label)

        nearby_filter_row = QHBoxLayout()
        nearby_filter_row.addWidget(QLabel("검색"))
        self.nearby_search_edit = QLineEdit()
        self.nearby_search_edit.setPlaceholderText("SSID / BSSID / 벤더")
        nearby_filter_row.addWidget(self.nearby_search_edit, 2)

        nearby_filter_row.addWidget(QLabel("대역"))
        self.nearby_band_filter = QComboBox()
        self.nearby_band_filter.addItem("전체", "all")
        self.nearby_band_filter.addItem("2.4 GHz", "2.4")
        self.nearby_band_filter.addItem("5 GHz", "5")
        self.nearby_band_filter.addItem("6 GHz", "6")
        nearby_filter_row.addWidget(self.nearby_band_filter)

        nearby_filter_row.addWidget(QLabel("보안"))
        self.nearby_security_filter = QComboBox()
        self.nearby_security_filter.addItem("전체", "all")
        self.nearby_security_filter.addItem("보안 사용", "secured")
        self.nearby_security_filter.addItem("개방형", "open")
        nearby_filter_row.addWidget(self.nearby_security_filter)

        nearby_filter_row.addWidget(QLabel("정렬"))
        self.nearby_sort_combo = QComboBox()
        self.nearby_sort_combo.addItem("신호 높은 순", "signal_desc")
        self.nearby_sort_combo.addItem("채널 낮은 순", "channel_asc")
        self.nearby_sort_combo.addItem("채널 사용률 높은 순", "utilization_desc")
        self.nearby_sort_combo.addItem("SSID 이름순", "ssid_asc")
        self.nearby_sort_combo.addItem("벤더 이름순", "vendor_asc")
        nearby_filter_row.addWidget(self.nearby_sort_combo)

        self.nearby_connected_only_check = QCheckBox("현재 연결 AP만")
        nearby_filter_row.addWidget(self.nearby_connected_only_check)
        nearby_layout.addLayout(nearby_filter_row)

        self.nearby_table = QTableWidget(0, 10)
        self.nearby_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.nearby_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.nearby_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.nearby_table.setAlternatingRowColors(True)
        self.nearby_table.setWordWrap(False)
        self.nearby_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.nearby_table.setSortingEnabled(True)
        self.nearby_table.setHorizontalHeaderLabels(
            ["SSID", "BSSID", "벤더", "신호", "무선 규격", "대역", "채널", "보안", "채널 사용률", "연결 단말"]
        )
        self.nearby_table.verticalHeader().setVisible(False)
        self._configure_nearby_table_columns()
        self.nearby_table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        nearby_layout.addWidget(self.nearby_table, 1)
        layout.addWidget(nearby_group, 2)

        self.refresh_button.clicked.connect(self.refresh_wireless_info)
        self.auto_refresh_check.toggled.connect(self._toggle_auto_refresh)
        self.interval_spin.valueChanged.connect(self._handle_interval_change)
        self.nearby_refresh_button.clicked.connect(self.refresh_nearby_access_points_quietly)
        self.nearby_auto_refresh_check.toggled.connect(self._toggle_nearby_auto_refresh)
        self.nearby_interval_spin.valueChanged.connect(self._handle_nearby_interval_change)
        self.nearby_refresh_oui_button.clicked.connect(self.refresh_nearby_oui_cache)
        self.nearby_search_edit.textChanged.connect(self._apply_nearby_view)
        self.nearby_band_filter.currentIndexChanged.connect(self._apply_nearby_view)
        self.nearby_security_filter.currentIndexChanged.connect(self._apply_nearby_view)
        self.nearby_sort_combo.currentIndexChanged.connect(self._handle_nearby_sort_change)
        self.nearby_connected_only_check.toggled.connect(self._apply_nearby_view)

    def _status_column_count(self) -> int:
        width = self.status_group.width() or self.width()
        if width >= 960:
            return 3
        if width >= 620:
            return 2
        return 1

    def _rebuild_status_grid(self) -> None:
        if not hasattr(self, "status_grid"):
            return

        for card in self.status_cards.values():
            self.status_grid.removeWidget(card)

        columns = self._status_column_count()
        max_columns = 3
        for column in range(max_columns):
            self.status_grid.setColumnStretch(column, 1 if column < columns else 0)

        for index, (key, _) in enumerate(self.info_fields):
            row = index // columns
            column = index % columns
            self.status_grid.addWidget(self.status_cards[key], row, column)

    def _configure_nearby_table_columns(self) -> None:
        header = self.nearby_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(56)

        stretch_columns = {0, 2, 7}
        fixed_widths = {
            1: 138,
            3: 64,
            4: 96,
            5: 66,
            6: 58,
            8: 88,
            9: 78,
        }

        for column in range(self.nearby_table.columnCount()):
            if column in stretch_columns:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                self.nearby_table.setColumnWidth(column, fixed_widths.get(column, 96))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild_status_grid()

    def _handle_auto_refresh_tick(self) -> None:
        self.refresh_wireless_info()

    def _handle_nearby_auto_refresh_tick(self) -> None:
        self.refresh_nearby_access_points_quietly()

    def refresh_wireless_info(self) -> None:
        if self._wireless_refresh_running:
            return
        self._wireless_refresh_running = True
        self._start_worker(
            self.state.wireless_service.get_wireless_info,
            on_result=self._update_wireless_view,
            on_finished=lambda: self._set_refresh_running("wireless", False),
            error_title="무선 상태 조회 실패",
        )

    def refresh_nearby_access_points_quietly(self) -> None:
        self.refresh_nearby_access_points()
        if self._nearby_refresh_running:
            self._update_nearby_summary(self._filtered_nearby_access_points())

    def refresh_nearby_access_points(self) -> None:
        if self._nearby_refresh_running:
            return
        self._nearby_refresh_running = True
        self.nearby_refresh_button.setEnabled(False)
        self._start_worker(
            self.state.wireless_service.scan_nearby_access_points,
            on_result=self._update_nearby_access_points,
            on_finished=lambda: self._set_refresh_running("nearby", False),
            error_title="주변 AP 조회 실패",
        )

    def refresh_nearby_oui_cache(self) -> None:
        self.nearby_summary_label.setText("OUI 캐시를 갱신하는 중...")
        self._start_worker(
            self.state.oui_service.refresh_cache,
            on_result=self._finish_nearby_oui_refresh,
            error_title="OUI 캐시 갱신 실패",
        )

    def _update_wireless_view(self, info: WirelessInfo) -> None:
        self.current_info = info
        self.info_labels["interface_name"].setText(info.interface_name or info.description or "-")
        self.info_labels["interface_name"].setToolTip(info.description or info.interface_name or "")
        self.info_labels["state"].setText(info.state or "-")
        self.info_labels["ssid"].setText(info.ssid or "-")
        self.info_labels["bssid"].setText(info.bssid or "-")
        self.info_labels["radio_type"].setText(info.radio_type or "-")
        self.info_labels["channel"].setText(info.channel or "-")
        self.info_labels["band"].setText(info.band or "-")
        self.info_labels["signal"].setText(info.signal_text)
        self.info_labels["receive_rate"].setText(f"{info.receive_rate_mbps} Mbps" if info.receive_rate_mbps else "-")
        self.info_labels["transmit_rate"].setText(f"{info.transmit_rate_mbps} Mbps" if info.transmit_rate_mbps else "-")

        state_lower = info.state.lower()
        state_color = "#1b5e20" if ("connected" in state_lower or "연결" in info.state) else "#b71c1c"
        self.info_labels["state"].setStyleSheet(f"font-size:13px; font-weight:700; color:{state_color};")
        self._log_wireless_changes(info)
        self.previous_info = info
        self._apply_nearby_view()

    def _update_nearby_access_points(self, access_points: list[NearbyAccessPoint]) -> None:
        self.nearby_access_points = access_points
        self._apply_nearby_view()

    def _apply_nearby_view(self) -> None:
        sort_state = self._capture_table_sort_state(self.nearby_table)
        if sort_state[0]:
            self.nearby_table.setSortingEnabled(False)
        self.nearby_table.setRowCount(0)
        filtered_access_points = self._filtered_nearby_access_points()
        current_bssid = self._normalize_bssid(self.current_info.bssid if self.current_info else "")

        for access_point in filtered_access_points:
            row = self.nearby_table.rowCount()
            self.nearby_table.insertRow(row)
            security = " / ".join(part for part in [access_point.authentication, access_point.encryption] if part) or "-"
            values = [
                access_point.ssid or "-",
                access_point.bssid or "-",
                access_point.vendor or "-",
                access_point.signal_text,
                access_point.radio_standard or "-",
                access_point.band or "-",
                access_point.channel or "-",
                security,
                (
                    f"{access_point.channel_utilization_percent}%"
                    if access_point.channel_utilization_percent is not None
                    else "-"
                ),
                str(access_point.connected_stations) if access_point.connected_stations is not None else "-",
            ]
            sort_values = [
                (access_point.ssid or "").lower(),
                self._normalize_bssid(access_point.bssid),
                (access_point.vendor or "").lower(),
                self._number_or_low(access_point.signal_percent),
                self._radio_standard_sort_value(access_point.radio_standard),
                self._band_sort_value(access_point.band),
                self._channel_sort_value(access_point.channel),
                security.lower(),
                self._number_or_low(access_point.channel_utilization_percent),
                self._number_or_low(access_point.connected_stations),
            ]

            is_current_ap = bool(current_bssid and self._normalize_bssid(access_point.bssid) == current_bssid)
            for column, value in enumerate(values):
                item = sortable_table_item(value, sort_values[column])
                if column == 3 and access_point.signal_percent is not None:
                    if access_point.signal_percent >= 70:
                        item.setForeground(QColor("#1b5e20"))
                    elif access_point.signal_percent >= 40:
                        item.setForeground(QColor("#ef6c00"))
                    else:
                        item.setForeground(QColor("#b71c1c"))
                if column in {1, 3, 4, 5, 6, 8, 9}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_current_ap:
                    item.setBackground(QColor("#e8f5e9"))
                    item.setToolTip("현재 연결된 AP")
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    if column != 3:
                        item.setForeground(QColor("#1b5e20"))
                self.nearby_table.setItem(row, column, item)

        self._restore_table_sort_state(self.nearby_table, sort_state)
        self._update_nearby_summary(filtered_access_points)

    def _handle_nearby_sort_change(self) -> None:
        sort_mode = str(self.nearby_sort_combo.currentData() or "signal_desc")
        column, order = {
            "signal_desc": (3, Qt.SortOrder.DescendingOrder),
            "channel_asc": (6, Qt.SortOrder.AscendingOrder),
            "utilization_desc": (8, Qt.SortOrder.DescendingOrder),
            "ssid_asc": (0, Qt.SortOrder.AscendingOrder),
            "vendor_asc": (2, Qt.SortOrder.AscendingOrder),
        }.get(sort_mode, (3, Qt.SortOrder.DescendingOrder))
        self.nearby_table.sortByColumn(column, order)
        self._apply_nearby_view()

    def _capture_table_sort_state(self, table: QTableWidget) -> tuple[bool, int, Qt.SortOrder]:
        header = table.horizontalHeader()
        return table.isSortingEnabled(), header.sortIndicatorSection(), header.sortIndicatorOrder()

    def _restore_table_sort_state(self, table: QTableWidget, sort_state: tuple[bool, int, Qt.SortOrder]) -> None:
        sorting_enabled, section, order = sort_state
        if not sorting_enabled:
            return
        table.setSortingEnabled(True)
        if 0 <= section < table.columnCount():
            table.sortItems(section, order)

    def _number_or_low(self, value: float | int | None) -> float:
        if value is None:
            return -1.0
        return float(value)

    def _band_sort_value(self, band_text: str) -> float:
        normalized = (band_text or "").replace(" ", "").lower()
        if "2.4" in normalized:
            return 2.4
        match = re.search(r"\d+(?:\.\d+)?", normalized)
        if not match:
            return -1.0
        return float(match.group(0))

    def _radio_standard_sort_value(self, radio_standard: str) -> tuple[int, int, str]:
        normalized = re.sub(r"\s+", "", radio_standard or "").lower()
        order = {
            "802.11be": 7,
            "802.11ax": 6,
            "802.11ac": 5,
            "802.11n": 4,
            "802.11g": 3,
            "802.11a": 2,
            "802.11b": 1,
        }
        for token, rank in order.items():
            if token in normalized:
                return (0, rank, normalized)
        return (1, 0, normalized)

    def _filtered_nearby_access_points(self) -> list[NearbyAccessPoint]:
        search_text = self.nearby_search_edit.text().strip().lower()
        band_filter = str(self.nearby_band_filter.currentData() or "all")
        security_filter = str(self.nearby_security_filter.currentData() or "all")
        connected_only = self.nearby_connected_only_check.isChecked()

        filtered: list[NearbyAccessPoint] = []
        for access_point in self.nearby_access_points:
            if search_text:
                haystack = " ".join(
                    [
                        access_point.ssid or "",
                        access_point.bssid or "",
                        access_point.vendor or "",
                        access_point.radio_standard or "",
                    ]
                ).lower()
                if search_text not in haystack:
                    continue

            if band_filter != "all" and not self._matches_band_filter(access_point.band, band_filter):
                continue

            if security_filter != "all" and self._security_category(access_point) != security_filter:
                continue

            if connected_only and not self._is_current_access_point(access_point):
                continue

            filtered.append(access_point)

        sort_mode = str(self.nearby_sort_combo.currentData() or "signal_desc")
        filtered.sort(key=lambda ap: self._nearby_sort_key(ap, sort_mode))
        return filtered

    def _update_nearby_summary(self, filtered_access_points: list[NearbyAccessPoint]) -> None:
        total_count = len(self.nearby_access_points)
        shown_count = len(filtered_access_points)
        cache_text = self.state.oui_service.cache_summary()

        if filtered_access_points:
            summary = summarize_channels(filtered_access_points)
        elif total_count:
            summary = "필터 조건에 맞는 AP가 없습니다."
        else:
            summary = "감지된 주변 AP가 없습니다."

        parts = [f"표시 {shown_count} / 전체 {total_count}"]
        if self._is_connected():
            ssid = self.current_info.ssid or "-"
            channel = self.current_info.channel or "-"
            parts.append(f"현재 연결 {ssid} / 채널 {channel}")
        parts.append(summary)
        parts.append(cache_text)
        self.nearby_summary_label.setText(" | ".join(parts))

    def _matches_band_filter(self, band_text: str, band_filter: str) -> bool:
        normalized = (band_text or "").replace(" ", "").lower()
        if band_filter == "2.4":
            return "2.4" in normalized
        if band_filter == "5":
            return normalized.startswith("5")
        if band_filter == "6":
            return normalized.startswith("6")
        return True

    def _security_category(self, access_point: NearbyAccessPoint) -> str:
        auth = (access_point.authentication or "").strip().lower()
        encryption = (access_point.encryption or "").strip().lower()

        if any(token in auth for token in ("open", "개방")):
            return "open"
        if any(token in encryption for token in ("none", "없음")) and not auth:
            return "open"
        if auth or encryption:
            return "secured"
        return "all"

    def _nearby_sort_key(self, access_point: NearbyAccessPoint, sort_mode: str) -> tuple:
        current_priority = 0 if self._is_current_access_point(access_point) else 1
        channel_number = self._channel_sort_value(access_point.channel)
        signal_value = access_point.signal_percent if access_point.signal_percent is not None else -1
        utilization = (
            access_point.channel_utilization_percent
            if access_point.channel_utilization_percent is not None
            else -1
        )
        ssid_value = (access_point.ssid or "").lower()
        vendor_value = (access_point.vendor or "").lower()

        if sort_mode == "channel_asc":
            return (current_priority, channel_number, -signal_value, ssid_value, vendor_value)
        if sort_mode == "utilization_desc":
            return (current_priority, -utilization, -signal_value, ssid_value)
        if sort_mode == "ssid_asc":
            return (current_priority, ssid_value, -signal_value, channel_number)
        if sort_mode == "vendor_asc":
            return (current_priority, vendor_value, ssid_value, -signal_value)
        return (current_priority, -signal_value, channel_number, ssid_value)

    def _is_current_access_point(self, access_point: NearbyAccessPoint) -> bool:
        current_bssid = self._normalize_bssid(self.current_info.bssid if self.current_info else "")
        if not current_bssid:
            return False
        return self._normalize_bssid(access_point.bssid) == current_bssid

    def _is_connected(self) -> bool:
        if self.current_info is None:
            return False
        state_lower = (self.current_info.state or "").lower()
        return bool(self.current_info.bssid and ("connected" in state_lower or "연결" in self.current_info.state))

    def _channel_sort_value(self, channel_text: str) -> int:
        match = re.search(r"\d+", channel_text or "")
        if not match:
            return 9999
        return int(match.group(0))

    def _normalize_bssid(self, bssid: str) -> str:
        return re.sub(r"[^0-9A-Fa-f]", "", bssid or "").lower()

    def _finish_nearby_oui_refresh(self, result) -> None:
        if result.success:
            self.refresh_nearby_access_points()
            return
        self.nearby_summary_label.setText(result.message)

    def _log_wireless_changes(self, current: WirelessInfo) -> None:
        if not self.previous_info:
            self._append_change_log(f"초기 상태: {current.state} / SSID={current.ssid or '-'}")
            return

        previous = self.previous_info
        if previous.state != current.state:
            self._append_change_log(f"상태 변경: {previous.state or '-'} -> {current.state or '-'}", QColor("#b71c1c"))
        if previous.ssid != current.ssid:
            self._append_change_log(f"SSID 변경: {previous.ssid or '-'} -> {current.ssid or '-'}", QColor("#ef6c00"))
        if previous.bssid != current.bssid:
            self._append_change_log(f"BSSID 변경: {previous.bssid or '-'} -> {current.bssid or '-'}", QColor("#ef6c00"))
        if previous.channel != current.channel:
            self._append_change_log(f"채널 변경: {previous.channel or '-'} -> {current.channel or '-'}", QColor("#ef6c00"))
        if previous.signal_percent is not None and current.signal_percent is not None and previous.signal_percent - current.signal_percent >= 15:
            self._append_change_log(f"신호 저하: {previous.signal_percent}% -> {current.signal_percent}%", QColor("#b71c1c"))

    def _append_change_log(self, message: str, color: QColor | None = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{timestamp}] {message}")
        if color:
            item.setForeground(color)
        self.change_log.insertItem(0, item)

    def _toggle_auto_refresh(self, enabled: bool) -> None:
        if enabled:
            self._handle_auto_refresh_tick()
            self.timer.start(self.interval_spin.value() * 1000)
        else:
            self.timer.stop()

    def _handle_interval_change(self, value: int) -> None:
        if self.auto_refresh_check.isChecked():
            self.timer.start(value * 1000)

    def _toggle_nearby_auto_refresh(self, enabled: bool) -> None:
        if enabled:
            self._handle_nearby_auto_refresh_tick()
            self.nearby_timer.start(self.nearby_interval_spin.value() * 1000)
        else:
            self.nearby_timer.stop()

    def _handle_nearby_interval_change(self, value: int) -> None:
        if self.nearby_auto_refresh_check.isChecked():
            self.nearby_timer.start(value * 1000)

    def _set_refresh_running(self, refresh_type: str, running: bool) -> None:
        if refresh_type == "wireless":
            self._wireless_refresh_running = running
            return
        if refresh_type == "nearby":
            self._nearby_refresh_running = running
            if hasattr(self, "nearby_refresh_button"):
                self.nearby_refresh_button.setEnabled(not running)

    def save_ui_state(self) -> dict:
        return {
            "auto_refresh": self.auto_refresh_check.isChecked(),
            "interval_sec": self.interval_spin.value(),
            "nearby_auto_refresh": self.nearby_auto_refresh_check.isChecked(),
            "nearby_interval_sec": self.nearby_interval_spin.value(),
            "nearby_search": self.nearby_search_edit.text().strip(),
            "nearby_band_filter": str(self.nearby_band_filter.currentData() or "all"),
            "nearby_security_filter": str(self.nearby_security_filter.currentData() or "all"),
            "nearby_sort": str(self.nearby_sort_combo.currentData() or "signal_desc"),
            "nearby_connected_only": self.nearby_connected_only_check.isChecked(),
        }

    def restore_ui_state(self, ui_state: dict | None) -> None:
        state = dict(ui_state or {})
        if not state:
            return

        interval_sec = int(state.get("interval_sec", self.interval_spin.value()) or self.interval_spin.value())
        self.interval_spin.setValue(max(1, min(30, interval_sec)))
        nearby_interval_sec = int(
            state.get("nearby_interval_sec", self.nearby_interval_spin.value()) or self.nearby_interval_spin.value()
        )
        self.nearby_interval_spin.setValue(max(5, min(300, nearby_interval_sec)))
        self.nearby_search_edit.setText(str(state.get("nearby_search", "") or ""))
        self._set_combo_data(self.nearby_band_filter, str(state.get("nearby_band_filter", "all") or "all"))
        self._set_combo_data(self.nearby_security_filter, str(state.get("nearby_security_filter", "all") or "all"))
        self._set_combo_data(self.nearby_sort_combo, str(state.get("nearby_sort", "signal_desc") or "signal_desc"))
        self.nearby_connected_only_check.setChecked(bool(state.get("nearby_connected_only", False)))
        self.auto_refresh_check.blockSignals(True)
        self.auto_refresh_check.setChecked(bool(state.get("auto_refresh", False)))
        self.auto_refresh_check.blockSignals(False)
        self.timer.stop()
        self.nearby_auto_refresh_check.blockSignals(True)
        self.nearby_auto_refresh_check.setChecked(bool(state.get("nearby_auto_refresh", False)))
        self.nearby_auto_refresh_check.blockSignals(False)
        self.nearby_timer.stop()
        self._apply_nearby_view()

    def _set_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _start_worker(
        self,
        fn: Callable,
        *args,
        on_result: Callable | None = None,
        on_progress: Callable | None = None,
        on_finished: Callable | None = None,
        error_title: str = "작업 실패",
        **kwargs,
    ) -> None:
        worker = FunctionWorker(fn, *args, **kwargs)
        self._active_workers.append(worker)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        if on_finished:
            worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(lambda text: QMessageBox.warning(self, error_title, text))
        worker.signals.finished.connect(lambda worker=worker: self._discard_worker(worker))
        self.state.thread_pool.start(worker)

    def _discard_worker(self, worker: FunctionWorker) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)
