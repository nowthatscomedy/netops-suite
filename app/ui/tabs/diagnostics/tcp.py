from __future__ import annotations

from threading import Event

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.result_models import TcpCheckResult
from app.utils.validators import ValidationError


class TcpDiagnosticsMixin:
    def _build_tcp_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("TCPing")
        form = QFormLayout(group)
        self.tcp_targets_edit = QPlainTextEdit()
        self.tcp_targets_edit.setMaximumHeight(110)
        self.tcp_targets_edit.setPlaceholderText("예:\nDNS,8.8.8.8\nGW,192.168.0.1")
        self.tcp_ports_edit = QLineEdit()
        self.tcp_ports_edit.setPlaceholderText("예: 22,80,443 또는 1-1024,2022")
        self.tcp_count_edit = QLineEdit()
        self.tcp_count_edit.setPlaceholderText("4")
        self.tcp_timeout_edit = QLineEdit()
        self.tcp_timeout_edit.setPlaceholderText(str(int(self.state.app_config.get("default_tcp_timeout_ms", 1000))))
        self.tcp_workers_edit = QLineEdit()
        self.tcp_workers_edit.setPlaceholderText(str(int(self.state.app_config.get("default_tcp_workers", 32))))
        self.tcp_continuous_check = QCheckBox("계속 실행 (-t)")

        button_row = QHBoxLayout()
        self.tcp_start_button = QPushButton("실행")
        self.tcp_cancel_button = QPushButton("중지")
        self.tcp_cancel_button.setEnabled(False)
        button_row.addWidget(self.tcp_start_button)
        button_row.addWidget(self.tcp_cancel_button)
        button_row.addStretch(1)

        form.addRow("대상", self.tcp_targets_edit)
        form.addRow("포트", self.tcp_ports_edit)
        form.addRow("횟수", self.tcp_count_edit)
        form.addRow("Timeout (ms)", self.tcp_timeout_edit)
        form.addRow("동시 실행 수", self.tcp_workers_edit)
        form.addRow("", self.tcp_continuous_check)
        form.addRow("", button_row)
        layout.addWidget(group)

        self.tcp_table = QTableWidget(0, 12)
        self.tcp_table.setHorizontalHeaderLabels(
            ["이름", "대상", "포트", "상태", "시도", "성공", "실패", "손실률", "최소(ms)", "평균(ms)", "최대(ms)", "최근 시각"]
        )
        self._setup_table(self.tcp_table)
        self._set_stretch_columns(self.tcp_table, 1)
        self.tcp_table.setSortingEnabled(True)

        self.tcp_log = self._output()
        self.tcp_log_panel = self._build_log_panel("실시간 로그", self.tcp_log)
        self.tcp_splitter = self._build_result_splitter(
            key="tcp",
            table=self.tcp_table,
            log_panel=self.tcp_log_panel,
        )
        layout.addWidget(self.tcp_splitter, 1)

        self.tcp_start_button.clicked.connect(self.start_tcp_check)
        self.tcp_cancel_button.clicked.connect(self.cancel_tcp_check)
        self.tcp_continuous_check.toggled.connect(lambda checked: self.tcp_count_edit.setEnabled(not checked))
        return page

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
