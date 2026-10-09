from __future__ import annotations

from datetime import datetime
import re
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.models.network_models import NearbyAccessPoint, WirelessInfo
from app.ui.common import (
    make_empty_state,
    make_inline_status,
    set_inline_status,
    set_table_minimums,
    sortable_table_item,
)
from app.utils.parser import summarize_channels
from app.utils.threading_utils import FunctionWorker


from netops_suite.ui.actions import ActionKind, make_action_button
from netops_suite.ui.numeric_inputs import NoWheelSpinBox
from netops_suite.ui.selection_inputs import NoWheelComboBox
from app.ui.common.disclosure import CollapsibleSection, make_page_header

class WirelessTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._active_workers: list[FunctionWorker] = []
        self._wireless_refresh_running = False
        self._nearby_refresh_running = False
        self._nearby_oui_refresh_running = False
        self._startup_refresh_requested = False
        self._shutting_down = False
        self.current_info: WirelessInfo | None = None
        self.previous_info: WirelessInfo | None = None
        self.nearby_access_points: list[NearbyAccessPoint] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._handle_auto_refresh_tick)
        self.nearby_timer = QTimer(self)
        self.nearby_timer.timeout.connect(self._handle_nearby_auto_refresh_tick)
        self._status_grid_timer = QTimer(self)
        self._status_grid_timer.setSingleShot(True)
        self._status_grid_timer.timeout.connect(self._rebuild_status_grid)

        self._build_ui()
        self._status_grid_timer.start(0)

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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(make_page_header("Wi-Fi 상태 확인", "현재 연결 상태를 확인하거나 주변 AP를 스캔하세요."))

        self.wireless_scroll_area = QScrollArea()
        self.wireless_scroll_area.setObjectName("wirelessWorkspaceScroll")
        self.wireless_scroll_area.setWidgetResizable(True)
        self.wireless_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.wireless_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.wireless_content = QWidget()
        self.wireless_content.setObjectName("wirelessWorkspaceContent")
        workspace_layout = QVBoxLayout(self.wireless_content)
        workspace_layout.setContentsMargins(0, 0, 6, 0)
        workspace_layout.setSpacing(16)
        workspace_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.wireless_scroll_area.setWidget(self.wireless_content)
        layout.addWidget(self.wireless_scroll_area, 1)

        top_widget = QWidget()
        top_row = QVBoxLayout(top_widget)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.status_group = QGroupBox("현재 Wi-Fi 상태")
        self.status_group.installEventFilter(self)
        status_layout = QVBoxLayout(self.status_group)
        controls = QHBoxLayout()
        self.refresh_button = make_action_button("새로고침", ActionKind.REFRESH, tooltip="현재 Wi-Fi 상태를 다시 불러옵니다.")
        self.auto_refresh_check = QCheckBox("자동 새로고침")
        self.interval_spin = NoWheelSpinBox()
        self.interval_spin.setRange(1, 30)
        self.interval_spin.setValue(int(self.state.app_config.get("wireless_refresh_interval_sec", 2)))
        self.interval_spin.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        status_layout.addLayout(controls)
        self.wireless_status_label = make_inline_status("info", "")
        self.wireless_status_label.setAccessibleName("현재 Wi-Fi 조회 상태")
        status_layout.addWidget(self.wireless_status_label)

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
        self.status_grid.setHorizontalSpacing(8)
        self.status_grid.setVerticalSpacing(6)

        for key, title in self.info_fields:
            card = QFrame()
            card.setObjectName("wirelessStatusCard")
            card.setMinimumHeight(80)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(6)

            title_label = QLabel(title)
            title_label.setObjectName("wirelessCardTitle")

            value_label = QLabel("-")
            value_label.setObjectName("wirelessCardValue")
            value_label.setWordWrap(True)
            value_label.setMinimumWidth(0)
            value_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

            card_layout.addWidget(title_label)
            card_layout.addWidget(value_label)

            self.info_labels[key] = value_label
            self.status_cards[key] = card

        status_layout.addLayout(self.status_grid)
        self.status_details_section = CollapsibleSection("연결 상세 정보")
        self.status_detail_grid = QGridLayout()
        self.status_detail_grid.setHorizontalSpacing(8)
        self.status_detail_grid.setVerticalSpacing(8)
        self.status_details_section.content_layout.addLayout(self.status_detail_grid)
        refresh_options = QHBoxLayout()
        refresh_options.addWidget(self.auto_refresh_check)
        refresh_options.addWidget(QLabel("주기(초)"))
        refresh_options.addWidget(self.interval_spin)
        refresh_options.addStretch(1)
        self.status_details_section.content_layout.addLayout(refresh_options)
        self.status_details_section.watch(self.auto_refresh_check)
        self.status_details_section.watch(self.interval_spin)
        status_layout.addWidget(self.status_details_section)
        top_row.addWidget(self.status_group)

        self.change_log_section = CollapsibleSection("연결 변화 로그")
        change_layout = self.change_log_section.content_layout
        self.change_log = QListWidget()
        self.change_log.setObjectName("wirelessChangeLog")
        self.change_log.setMinimumHeight(160)
        self.change_log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.change_log.setWordWrap(True)
        change_layout.addWidget(self.change_log)
        top_row.addWidget(self.change_log_section)

        self.nearby_group = QGroupBox("주변 AP / 채널 현황")
        nearby_layout = QVBoxLayout(self.nearby_group)

        nearby_controls = QHBoxLayout()
        self.nearby_refresh_button = make_action_button("스캔", ActionKind.START, tooltip="주변 AP를 스캔합니다.")
        self.nearby_refresh_oui_button = make_action_button("OUI 갱신", ActionKind.REFRESH, tooltip="OUI 캐시를 업데이트합니다.")
        self.nearby_summary_label = QLabel("스캔 전")
        self.nearby_auto_refresh_check = QCheckBox("자동 새로고침")
        self.nearby_interval_spin = NoWheelSpinBox()
        self.nearby_interval_spin.setRange(5, 300)
        self.nearby_interval_spin.setValue(int(self.state.app_config.get("wireless_nearby_refresh_interval_sec", 30)))
        self.nearby_interval_spin.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        self.nearby_summary_label.setWordWrap(True)
        self.nearby_summary_label.setMinimumWidth(0)
        self.nearby_summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.nearby_summary_label.setStyleSheet("color:#475467;")
        nearby_controls.addWidget(self.nearby_refresh_button)
        nearby_controls.addStretch(1)
        nearby_layout.addLayout(nearby_controls)
        nearby_layout.addWidget(self.nearby_summary_label)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("검색"))
        self.nearby_search_edit = QLineEdit()
        self.nearby_search_edit.setPlaceholderText("SSID / BSSID / 제조사(vendor)")
        self.nearby_search_edit.setAccessibleName("주변 AP 검색")
        search_row.addWidget(self.nearby_search_edit, 2)
        nearby_layout.addLayout(search_row)
        self.nearby_options_section = CollapsibleSection("필터 및 스캔 옵션")
        nearby_filter_row = QGridLayout()
        nearby_filter_row.setColumnStretch(1, 1)
        nearby_filter_row.setColumnStretch(3, 1)

        nearby_filter_row.addWidget(QLabel("대역"), 0, 0)
        self.nearby_band_filter = NoWheelComboBox()
        self.nearby_band_filter.addItem("전체", "all")
        self.nearby_band_filter.addItem("2.4 GHz", "2.4")
        self.nearby_band_filter.addItem("5 GHz", "5")
        self.nearby_band_filter.addItem("6 GHz", "6")
        nearby_filter_row.addWidget(self.nearby_band_filter, 0, 1)

        nearby_filter_row.addWidget(QLabel("보안"), 0, 2)
        self.nearby_security_filter = NoWheelComboBox()
        self.nearby_security_filter.addItem("전체", "all")
        self.nearby_security_filter.addItem("보안 사용", "secured")
        self.nearby_security_filter.addItem("개방형", "open")
        nearby_filter_row.addWidget(self.nearby_security_filter, 0, 3)

        nearby_filter_row.addWidget(QLabel("정렬"), 1, 0)
        self.nearby_sort_combo = NoWheelComboBox()
        self.nearby_sort_combo.addItem("신호 높은 순", "signal_desc")
        self.nearby_sort_combo.addItem("채널 낮은 순", "channel_asc")
        self.nearby_sort_combo.addItem("채널 사용률 높은 순", "utilization_desc")
        self.nearby_sort_combo.addItem("SSID 이름순", "ssid_asc")
        self.nearby_sort_combo.addItem("제조사(vendor) 이름순", "vendor_asc")
        nearby_filter_row.addWidget(self.nearby_sort_combo, 1, 1, 1, 3)

        self.nearby_connected_only_check = QCheckBox("현재 연결 AP만")
        nearby_filter_row.addWidget(self.nearby_connected_only_check, 2, 0, 1, 2)
        self.nearby_column_button = QToolButton()
        self.nearby_column_button.setText("컬럼")
        self.nearby_column_button.setPopupMode(QToolButton.InstantPopup)
        self.nearby_column_menu = QMenu(self.nearby_column_button)
        self.nearby_column_button.setMenu(self.nearby_column_menu)
        nearby_filter_row.addWidget(self.nearby_column_button, 2, 2, 1, 2, Qt.AlignmentFlag.AlignRight)
        self.nearby_options_section.content_layout.addLayout(nearby_filter_row)
        nearby_options_row = QHBoxLayout()
        nearby_options_row.addWidget(self.nearby_auto_refresh_check)
        nearby_options_row.addWidget(QLabel("주기(초)"))
        nearby_options_row.addWidget(self.nearby_interval_spin)
        nearby_options_row.addWidget(self.nearby_refresh_oui_button)
        nearby_options_row.addStretch(1)
        self.nearby_options_section.content_layout.addLayout(nearby_options_row)
        for option in (self.nearby_band_filter, self.nearby_security_filter,
                       self.nearby_sort_combo, self.nearby_connected_only_check,
                       self.nearby_auto_refresh_check, self.nearby_interval_spin):
            self.nearby_options_section.watch(option)
        nearby_layout.addWidget(self.nearby_options_section)

        self.nearby_table = QTableWidget(0, 10)
        self.nearby_table.setObjectName("wirelessNearbyTable")
        self.nearby_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.nearby_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.nearby_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.nearby_table.setAlternatingRowColors(True)
        self.nearby_table.setWordWrap(False)
        self.nearby_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.nearby_table.setSortingEnabled(True)
        self.nearby_table.setHorizontalHeaderLabels(
            ["SSID", "BSSID", "제조사(vendor)", "신호", "무선 규격", "대역", "채널", "보안", "채널 사용률", "연결 단말"]
        )
        self.nearby_table.verticalHeader().setVisible(False)
        self._configure_nearby_table_columns()
        set_table_minimums(self.nearby_table, 280)
        self._build_nearby_column_menu()
        self.nearby_table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.nearby_empty_label = make_empty_state(
            "주변 AP 스캔을 눌러 무선 네트워크를 확인하세요. Wi-Fi가 꺼져 있으면 결과가 없을 수 있습니다."
        )
        nearby_layout.addWidget(self.nearby_empty_label)
        nearby_layout.addWidget(self.nearby_table, 1)
        workspace_layout.addWidget(top_widget)
        workspace_layout.addWidget(self.nearby_group, 1)

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
        if width >= 880:
            return 4
        return 2

    def _rebuild_status_grid(self) -> None:
        if not hasattr(self, "status_grid"):
            return

        columns = self._status_column_count()
        if columns == getattr(self, "_status_grid_columns", None):
            return
        self._status_grid_columns = columns

        for card in self.status_cards.values():
            self.status_grid.removeWidget(card)
            self.status_detail_grid.removeWidget(card)

        max_columns = 4
        for column in range(max_columns):
            self.status_grid.setColumnStretch(column, 1 if column < columns else 0)
            self.status_detail_grid.setColumnStretch(column, 1 if column < columns else 0)

        primary_keys = ("ssid", "state", "signal", "channel")
        for index, key in enumerate(primary_keys):
            self.status_grid.addWidget(self.status_cards[key], index // columns, index % columns)
        detail_keys = [key for key, _ in self.info_fields if key not in primary_keys]
        for index, key in enumerate(detail_keys):
            self.status_detail_grid.addWidget(self.status_cards[key], index // columns, index % columns)

    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self, "status_group", None) and event.type() == QEvent.Type.Resize:
            self._status_grid_timer.start(0)
        return super().eventFilter(watched, event)

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

    def _set_info_label(self, key: str, text: str, tooltip: str | None = None) -> None:
        value = text or "-"
        label = self.info_labels[key]
        label.setText(value)
        label.setToolTip(tooltip if tooltip is not None else (value if value != "-" else ""))

    @staticmethod
    def _signal_strength(signal_percent: int | None) -> tuple[str, str]:
        if signal_percent is None:
            return "", ""
        if signal_percent >= 70:
            return "#1b5e20", "좋음"
        if signal_percent >= 40:
            return "#ef6c00", "주의"
        return "#b71c1c", "약함"

    def _update_signal_label(self, info: WirelessInfo) -> None:
        label = self.info_labels["signal"]
        connected = info.state.strip().lower() in {"connected", "연결됨"}
        color, strength = self._signal_strength(info.signal_percent if connected else None)
        value = info.signal_text if connected else "-"
        description = "신호 정보 없음" if connected else "Wi-Fi 미연결 · 신호 정보 없음"
        if strength:
            value = f"{value} · {strength}"
            description = (
                f"현재 Wi-Fi 신호 {value}\n"
                "70% 이상: 좋음 · 40~69%: 주의 · 40% 미만: 약함\n"
                "신호 백분율은 실제 처리량을 나타내지 않습니다."
            )
        elif connected and info.rssi:
            description = f"현재 Wi-Fi 신호 {value} · 백분율 정보 없음"
        self._set_info_label("signal", value, description)
        label.setStyleSheet(f"color: {color};" if color else "")
        label.setAccessibleName("현재 Wi-Fi 신호")
        label.setAccessibleDescription(description)

    def refresh_wireless_info(self) -> None:
        if self._wireless_refresh_running:
            return
        self._set_refresh_running("wireless", True)
        set_inline_status(
            self.wireless_status_label,
            "info",
            "현재 Wi-Fi 상태를 조회하는 중입니다...",
        )
        self._start_worker(
            self.state.wireless_service.get_wireless_info,
            on_result=self._update_wireless_view,
            on_finished=lambda: self._set_refresh_running("wireless", False),
            on_error=self._handle_wireless_refresh_error,
        )

    def refresh_nearby_access_points_quietly(self) -> None:
        self.refresh_nearby_access_points()

    def refresh_nearby_access_points(self) -> None:
        if self._nearby_refresh_running:
            return
        self._nearby_refresh_running = True
        self.nearby_refresh_button.setEnabled(False)
        self.nearby_summary_label.setStyleSheet("color:#475467;")
        self.nearby_summary_label.setText("주변 AP를 스캔하는 중입니다...")
        self._start_worker(
            self.state.wireless_service.scan_nearby_access_points,
            on_result=self._update_nearby_access_points,
            on_finished=lambda: self._set_refresh_running("nearby", False),
            on_error=self._handle_nearby_refresh_error,
        )

    def refresh_nearby_oui_cache(self) -> None:
        if self._nearby_oui_refresh_running:
            return
        self._nearby_oui_refresh_running = True
        self.nearby_refresh_oui_button.setEnabled(False)
        self.nearby_summary_label.setStyleSheet("color:#475467;")
        self.nearby_summary_label.setText("OUI 캐시를 업데이트하는 중...")
        self._start_worker(
            self.state.oui_service.refresh_cache,
            on_result=self._finish_nearby_oui_refresh,
            on_finished=self._finish_nearby_oui_refresh_operation,
            on_error=self._handle_nearby_oui_refresh_error,
        )

    def _update_wireless_view(self, info: WirelessInfo) -> None:
        self.current_info = info
        self._set_info_label("interface_name", info.interface_name or info.description or "-", info.description or info.interface_name or "")
        self._set_info_label("state", info.state or "-")
        self._set_info_label("ssid", info.ssid or "-")
        self._set_info_label("bssid", info.bssid or "-")
        self._set_info_label("radio_type", info.radio_type or "-")
        self._set_info_label("channel", info.channel or "-")
        self._set_info_label("band", info.band or "-")
        self._update_signal_label(info)
        self._set_info_label("receive_rate", f"{info.receive_rate_mbps} Mbps" if info.receive_rate_mbps else "-")
        self._set_info_label("transmit_rate", f"{info.transmit_rate_mbps} Mbps" if info.transmit_rate_mbps else "-")

        self._log_wireless_changes(info)
        self.previous_info = info
        self._apply_nearby_view()
        set_inline_status(
            self.wireless_status_label,
            "success",
            f"현재 Wi-Fi 상태 갱신 완료 · {datetime.now():%H:%M:%S}",
        )

    def _update_nearby_access_points(self, access_points: list[NearbyAccessPoint]) -> None:
        self.nearby_access_points = access_points
        self.nearby_summary_label.setStyleSheet("color:#475467;")
        self._apply_nearby_view()

    def _apply_nearby_view(self) -> None:
        selected_item = self.nearby_table.item(self.nearby_table.currentRow(), 1)
        selected_bssid = self._normalize_bssid(selected_item.text()) if selected_item else ""
        previous_scroll = self.nearby_table.verticalScrollBar().value()
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
                item.setToolTip(value)
                if column == 3:
                    color, strength = self._signal_strength(access_point.signal_percent)
                    signal_description = f"신호 {value} · {strength}" if strength else "신호 정보 없음"
                    item.setToolTip(signal_description)
                    item.setData(Qt.ItemDataRole.AccessibleTextRole, signal_description)
                    if color:
                        item.setForeground(QColor(color))
                if column in {1, 3, 4, 5, 6, 8, 9}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_current_ap:
                    item.setBackground(QColor("#e8f5e9"))
                    item.setToolTip(f"현재 연결된 AP\n{item.toolTip()}")
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    if column != 3:
                        item.setForeground(QColor("#1b5e20"))
                self.nearby_table.setItem(row, column, item)

        self._restore_table_sort_state(self.nearby_table, sort_state)
        if selected_bssid:
            for row in range(self.nearby_table.rowCount()):
                item = self.nearby_table.item(row, 1)
                if item and self._normalize_bssid(item.text()) == selected_bssid:
                    self.nearby_table.selectRow(row)
                    break
        self.nearby_table.verticalScrollBar().setValue(previous_scroll)
        self.nearby_empty_label.setVisible(not filtered_access_points)
        self._update_nearby_summary(filtered_access_points)

    def _build_nearby_column_menu(self) -> None:
        self.nearby_column_actions: list[QAction] = []
        for column in range(self.nearby_table.columnCount()):
            header_item = self.nearby_table.horizontalHeaderItem(column)
            title = header_item.text() if header_item else f"컬럼 {column + 1}"
            action = QAction(title, self.nearby_column_menu)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda checked, column=column: self._set_nearby_column_visible(column, checked))
            self.nearby_column_menu.addAction(action)
            self.nearby_column_actions.append(action)

    def _set_nearby_column_visible(self, column: int, visible: bool) -> None:
        if not visible:
            visible_columns = [
                index
                for index in range(self.nearby_table.columnCount())
                if index != column and not self.nearby_table.isColumnHidden(index)
            ]
            if not visible_columns:
                action = self.nearby_column_actions[column]
                action.blockSignals(True)
                action.setChecked(True)
                action.blockSignals(False)
                return
        self.nearby_table.setColumnHidden(column, not visible)

    def _restore_nearby_hidden_columns(self, hidden_columns: object) -> None:
        if not isinstance(hidden_columns, list):
            return
        hidden = {int(column) for column in hidden_columns if str(column).isdigit()}
        for column, action in enumerate(getattr(self, "nearby_column_actions", [])):
            visible = column not in hidden
            self.nearby_table.setColumnHidden(column, not visible)
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)

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
            full_summary = summarize_channels(filtered_access_points)
            summary = self._compact_channel_summary(filtered_access_points)
        elif total_count:
            full_summary = "필터 조건에 맞는 AP가 없습니다."
            summary = "필터 조건에 맞는 AP가 없습니다."
        else:
            full_summary = "감지된 주변 AP가 없습니다."
            summary = "감지된 주변 AP가 없습니다."

        parts = [f"표시 {shown_count} / 전체 {total_count}"]
        if self._is_connected():
            ssid = self.current_info.ssid or "-"
            channel = self.current_info.channel or "-"
            parts.append(f"현재 연결 {ssid} / 채널 {channel}")
        parts.append(summary)
        self.nearby_summary_label.setText(" | ".join(parts))
        self.nearby_summary_label.setToolTip(f"{full_summary}\n\n{cache_text}")
        self.nearby_refresh_oui_button.setToolTip(f"OUI 캐시를 업데이트합니다.\n{cache_text}")

    def _compact_channel_summary(
        self, access_points: list[NearbyAccessPoint]
    ) -> str:
        band_counts: dict[str, int] = {}
        channel_counts: dict[str, int] = {}
        for access_point in access_points:
            band = access_point.band or "알 수 없음"
            channel = access_point.channel or "-"
            band_counts[band] = band_counts.get(band, 0) + 1
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
        bands = ", ".join(
            f"{band} {count}개"
            for band, count in sorted(band_counts.items())
        )
        busiest = sorted(
            channel_counts.items(),
            key=lambda item: (-item[1], self._channel_sort_value(item[0])),
        )[:3]
        channels = ", ".join(
            f"{channel}({count})" for channel, count in busiest
        )
        return f"{bands} · 주요 채널 {channels}"

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
        self.nearby_summary_label.setStyleSheet("color:#b42318;")
        self.nearby_summary_label.setText(result.message)

    def _finish_nearby_oui_refresh_operation(self) -> None:
        self._nearby_oui_refresh_running = False
        self.nearby_refresh_oui_button.setEnabled(True)

    def _handle_wireless_refresh_error(self, message: str) -> None:
        detail = str(message).strip() or "현재 Wi-Fi 상태 조회에 실패했습니다."
        set_inline_status(
            self.wireless_status_label,
            "error",
            f"Wi-Fi 상태 조회 실패: {detail} · 새로고침으로 다시 시도해 주세요.",
        )

    def _handle_nearby_refresh_error(self, message: str) -> None:
        detail = str(message).strip() or "주변 AP 조회에 실패했습니다."
        self.nearby_summary_label.setStyleSheet("color:#b42318;")
        self.nearby_summary_label.setText(
            f"주변 AP 조회 실패: {detail} · 스캔을 눌러 다시 시도해 주세요."
        )

    def _handle_nearby_oui_refresh_error(self, message: str) -> None:
        detail = str(message).strip() or "OUI 캐시 업데이트에 실패했습니다."
        self.nearby_summary_label.setStyleSheet("color:#b42318;")
        self.nearby_summary_label.setText(
            f"OUI 캐시 업데이트 실패: {detail} · 다시 시도해 주세요."
        )

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
            if hasattr(self, "refresh_button"):
                self.refresh_button.setEnabled(not running)
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
            "details_expanded": self.status_details_section.isExpanded(),
            "change_log_expanded": self.change_log_section.isExpanded(),
            "nearby_options_expanded": self.nearby_options_section.isExpanded(),
            "nearby_hidden_columns": [
                column for column in range(self.nearby_table.columnCount()) if self.nearby_table.isColumnHidden(column)
            ],
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
        self._restore_nearby_hidden_columns(state.get("nearby_hidden_columns", []))
        self.status_details_section.setExpanded(bool(state.get("details_expanded", False)))
        self.change_log_section.setExpanded(bool(state.get("change_log_expanded", False)))
        self.nearby_options_section.setExpanded(bool(state.get("nearby_options_expanded", False)))
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
        on_error: Callable[[str], None] | None = None,
        error_title: str = "작업 실패",
        **kwargs,
    ) -> None:
        if self._shutting_down:
            return
        worker = FunctionWorker(fn, *args, **kwargs)
        self._active_workers.append(worker)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_progress:
            worker.signals.progress.connect(on_progress)
        if on_finished:
            worker.signals.finished.connect(on_finished)
        if on_error is not None:
            worker.signals.error.connect(on_error)
        else:
            worker.signals.error.connect(
                lambda text: QMessageBox.warning(self, error_title, text)
            )
        worker.signals.finished.connect(lambda worker=worker: self._discard_worker(worker))
        try:
            self.state.thread_pool.start(worker)
        except Exception as exc:
            self._discard_worker(worker)
            if on_error is not None:
                on_error(str(exc))
            else:
                QMessageBox.warning(self, error_title, str(exc))
            if on_finished:
                on_finished()

    def _discard_worker(self, worker: FunctionWorker) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.timer.stop()
        self.nearby_timer.stop()
        self._status_grid_timer.stop()
