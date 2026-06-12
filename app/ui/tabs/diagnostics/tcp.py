from __future__ import annotations

from threading import Event

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.result_models import TcpCheckResult
from app.ui.common import make_empty_state, make_inline_status, set_inline_status
from app.utils.validators import ValidationError


from netops_suite.ui.actions import ActionKind, make_action_button

class TcpDiagnosticsMixin:
    def _build_tcp_tab(self) -> QWidget:
        page = QScrollArea()
        page.setObjectName("tcpScrollArea")
        page.setWidgetResizable(True)
        page.setFrameShape(QScrollArea.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        page.setStyleSheet("QScrollArea#tcpScrollArea { background:#ffffff; border:0; }")
        page.viewport().setObjectName("tcpScrollAreaViewport")
        page.viewport().setStyleSheet("background:#ffffff;")
        content = QWidget()
        content.setObjectName("tcpPageContent")
        content.setStyleSheet("QWidget#tcpPageContent { background:#ffffff; }")
        layout = QVBoxLayout(content)
        self.tcp_scroll_area = page
        self.tcp_page_content = content

        group = QGroupBox("포트 연결 확인 (TCPing)")
        self.tcp_input_group = group
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        form = QGridLayout(group)
        form.setColumnStretch(1, 1)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        self.tcp_targets_edit = QPlainTextEdit()
        target_height = self.tcp_targets_edit.fontMetrics().lineSpacing() * 3 + 18
        self.tcp_targets_edit.setMinimumHeight(target_height)
        self.tcp_targets_edit.setMaximumHeight(target_height + self.tcp_targets_edit.fontMetrics().lineSpacing())
        self.tcp_targets_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tcp_targets_edit.setPlaceholderText("DNS,8.8.8.8\nGW,192.168.0.1\n192.168.0.254")
        self.tcp_targets_edit.setToolTip(
            "한 줄에 하나씩 입력합니다. 형식: 이름,IP 또는 IP. "
            "이름은 결과표의 이름 열에 표시됩니다."
        )
        self.tcp_targets_help_label = QLabel(
            "한 줄에 하나씩 입력합니다. 형식: 이름,IP 또는 IP. "
            "이름은 결과표의 이름 열에 표시되고, 생략하면 대상 주소가 이름으로 사용됩니다."
        )
        self.tcp_targets_help_label.setObjectName("tcpTargetsHelpLabel")
        self.tcp_targets_help_label.setWordWrap(True)
        self.tcp_targets_help_label.setStyleSheet("color:#667085; padding:2px 2px 0 2px;")
        self.tcp_ports_edit = QLineEdit()
        self.tcp_ports_edit.setPlaceholderText("예: 22,80,443 또는 8000-8010")
        self.tcp_ports_help_label = QLabel(
            "쉼표/공백/세미콜론으로 여러 포트를 입력하거나 범위를 입력합니다. "
            "예: 22,80,443 또는 8000-8010. 대상 × 포트 조합별로 확인합니다."
        )
        self.tcp_ports_help_label.setObjectName("tcpPortsHelpLabel")
        self.tcp_ports_help_label.setWordWrap(True)
        self.tcp_ports_help_label.setStyleSheet("color:#667085; padding:2px 2px 0 2px;")
        self.tcp_count_edit = QLineEdit()
        self.tcp_count_edit.setPlaceholderText("4")
        self.tcp_count_edit.setMaximumWidth(110)
        self.tcp_timeout_edit = QLineEdit()
        self.tcp_timeout_edit.setPlaceholderText(str(int(self.state.app_config.get("default_tcp_timeout_ms", 1000))))
        self.tcp_timeout_edit.setMaximumWidth(110)
        self.tcp_workers_edit = QLineEdit()
        self.tcp_workers_edit.setPlaceholderText(str(int(self.state.app_config.get("default_tcp_workers", 32))))
        self.tcp_workers_edit.setMaximumWidth(110)
        self.tcp_continuous_check = QCheckBox("계속 실행 (-t)")
        self.tcp_continuous_hint = make_inline_status("warning", "")

        options_row = QHBoxLayout()
        options_row.setSpacing(8)
        options_row.addWidget(QLabel("횟수"))
        options_row.addWidget(self.tcp_count_edit)
        options_row.addWidget(QLabel("Timeout (ms)"))
        options_row.addWidget(self.tcp_timeout_edit)
        options_row.addWidget(QLabel("동시 실행 수"))
        options_row.addWidget(self.tcp_workers_edit)
        options_row.addStretch(1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self.tcp_continuous_check)
        self.tcp_start_button = make_action_button("실행", ActionKind.START, tooltip="TCPing 방식으로 포트 연결 여부를 확인합니다.")
        self.tcp_cancel_button = make_action_button("중지", ActionKind.STOP)
        self.tcp_cancel_button.setEnabled(False)
        button_row.addWidget(self.tcp_start_button)
        button_row.addWidget(self.tcp_cancel_button)
        button_row.addStretch(1)

        form.addWidget(self.tcp_targets_edit, 0, 1)
        form.addWidget(self.tcp_targets_help_label, 1, 1)
        form.addWidget(QLabel("대상 목록"), 0, 0, 2, 1, alignment=Qt.AlignmentFlag.AlignTop)
        form.addWidget(self.tcp_ports_edit, 2, 1)
        form.addWidget(QLabel("포트"), 2, 0)
        form.addWidget(self.tcp_ports_help_label, 3, 1)
        form.addWidget(QLabel("실행 조건"), 4, 0)
        form.addLayout(options_row, 4, 1)
        form.addLayout(button_row, 5, 1)
        form.addWidget(self.tcp_continuous_hint, 6, 1)
        layout.addWidget(group)

        self.tcp_table = QTableWidget(0, 12)
        self.tcp_table.setHorizontalHeaderLabels(
            ["이름", "대상", "포트", "상태", "시도", "성공", "실패", "손실률", "최소(ms)", "평균(ms)", "최대(ms)", "최근 시각"]
        )
        self._setup_table(self.tcp_table)
        self._set_stretch_columns(self.tcp_table, 1)
        self.tcp_table.setSortingEnabled(True)
        self.tcp_empty_label = make_empty_state("대상과 포트를 입력하고 실행을 누르면 결과가 표시됩니다.")

        self.tcp_log = self._output()
        self.tcp_log_panel = self._build_log_panel("실시간 로그", self.tcp_log)
        self.tcp_splitter = self._build_result_splitter(
            key="tcp",
            table=self.tcp_table,
            log_panel=self.tcp_log_panel,
        )
        layout.addWidget(self.tcp_empty_label)
        layout.addWidget(self.tcp_splitter, 1)

        self.tcp_start_button.clicked.connect(self.start_tcp_check)
        self.tcp_cancel_button.clicked.connect(self.cancel_tcp_check)
        self.tcp_continuous_check.toggled.connect(self._toggle_tcp_continuous)
        page.setWidget(content)
        return page

    def _toggle_tcp_continuous(self, checked: bool) -> None:
        self.tcp_count_edit.setEnabled(not checked)
        set_inline_status(
            self.tcp_continuous_hint,
            "warning",
            "중지를 누를 때까지 포트 확인이 계속 실행됩니다." if checked else "",
        )

    def start_tcp_check(self) -> None:
        try:
            count = self._positive_int_or_default(self.tcp_count_edit, "TCP 횟수", 4)
            timeout_ms = self._positive_int_or_default(
                self.tcp_timeout_edit,
                "TCP Timeout",
                int(self.state.app_config.get("default_tcp_timeout_ms", 1000)),
            )
            workers = self._positive_int_or_default(
                self.tcp_workers_edit,
                "동시 실행 수",
                int(self.state.app_config.get("default_tcp_workers", 32)),
            )
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        self.tcp_results = []
        self.tcp_row_map.clear()
        self.tcp_log_lines.clear()
        self.tcp_table.setRowCount(0)
        self.tcp_empty_label.setVisible(False)
        self.tcp_log.clear()
        self.tcp_cancel_event = Event()
        self._set_tcp_running(True)

        self._start_worker(
            self.state.tcp_check_service.run_multi_check,
            self.tcp_targets_edit.toPlainText(),
            self.tcp_ports_edit.text(),
            count,
            timeout_ms,
            workers,
            self.tcp_continuous_check.isChecked(),
            cancel_event=self.tcp_cancel_event,
            on_progress=self._handle_tcp_progress,
            on_result=self._finish_tcp,
            on_finished=lambda: self._set_tcp_running(False),
            error_title="TCPing 실행 실패",
        )

    def _handle_tcp_progress(self, event: dict) -> None:
        result: TcpCheckResult = event["result"]
        line = event["line"]
        key = (result.name, result.target, result.port)
        self.tcp_log.appendPlainText(line)
        self.tcp_log_lines.setdefault(key, []).append(line)

        sort_state = self._capture_sort_state(self.tcp_table)
        if sort_state[0]:
            self.tcp_table.setSortingEnabled(False)

        row = self._find_tcp_row(key)
        if row is None:
            row = self.tcp_table.rowCount()
            self.tcp_table.insertRow(row)
            self.tcp_row_map[key] = row
            self.tcp_empty_label.setVisible(False)

        values = [
            result.name,
            result.target,
            str(result.port),
            result.status,
            str(result.sent),
            str(result.successful),
            str(result.failed),
            f"{result.packet_loss:.0f}%",
            f"{result.min_response_ms:.2f}" if result.min_response_ms is not None else "-",
            f"{result.response_ms:.2f}" if result.response_ms is not None else "-",
            f"{result.max_response_ms:.2f}" if result.max_response_ms is not None else "-",
            result.last_seen or "-",
        ]
        sort_values = [
            result.name.casefold(),
            result.target.casefold(),
            result.port,
            result.status.casefold(),
            result.sent,
            result.successful,
            result.failed,
            result.packet_loss,
            self._nullable_number_sort_value(result.min_response_ms),
            self._nullable_number_sort_value(result.response_ms),
            self._nullable_number_sort_value(result.max_response_ms),
            result.last_seen or "",
        ]
        for column, value in enumerate(values):
            item = self._sortable_table_item(value, sort_values[column])
            if column == 3:
                if result.status == "열림":
                    item.setForeground(QColor("#1b5e20"))
                elif result.status == "부분 응답":
                    item.setForeground(QColor("#ef6c00"))
                else:
                    item.setForeground(QColor("#b71c1c"))
            self.tcp_table.setItem(row, column, item)
        self.tcp_row_map[key] = row
        self._restore_sort_state(self.tcp_table, sort_state)
        self._rebuild_tcp_row_map()

    def _finish_tcp(self, results: list[TcpCheckResult]) -> None:
        self.tcp_results = results
        self.tcp_empty_label.setVisible(not bool(results))

    def _set_tcp_running(self, running: bool) -> None:
        self.tcp_start_button.setEnabled(not running)
        self.tcp_cancel_button.setEnabled(running)

    def cancel_tcp_check(self) -> None:
        if self.tcp_cancel_event:
            self.tcp_cancel_event.set()

    def _find_tcp_row(self, key: tuple[str, str, int]) -> int | None:
        mapped_row = self.tcp_row_map.get(key)
        if mapped_row is not None and self._tcp_row_matches(mapped_row, key):
            return mapped_row
        for row in range(self.tcp_table.rowCount()):
            if self._tcp_row_matches(row, key):
                return row
        return None

    def _tcp_row_matches(self, row: int, key: tuple[str, str, int]) -> bool:
        if row < 0 or row >= self.tcp_table.rowCount():
            return False
        return (
            self._cell(self.tcp_table, row, 0) == key[0]
            and self._cell(self.tcp_table, row, 1) == key[1]
            and self._cell(self.tcp_table, row, 2) == str(key[2])
        )

    def _rebuild_tcp_row_map(self) -> None:
        self.tcp_row_map.clear()
        for row in range(self.tcp_table.rowCount()):
            name = self._cell(self.tcp_table, row, 0)
            target = self._cell(self.tcp_table, row, 1)
            port_text = self._cell(self.tcp_table, row, 2)
            try:
                port = int(port_text)
            except ValueError:
                continue
            if name or target:
                self.tcp_row_map[(name, target, port)] = row
