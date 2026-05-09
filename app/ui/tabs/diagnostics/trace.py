from __future__ import annotations

from threading import Event

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.network_models import TraceHop
from app.models.result_models import OperationResult
from app.utils.parser import parse_trace_hop_line, parse_trace_hops
from app.utils.validators import ValidationError, validate_host_input


class TraceDiagnosticsMixin:
    def _build_trace_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("경로 추적")
        form = QFormLayout(group)
        self.trace_target_edit = QLineEdit()
        self.trace_target_edit.setPlaceholderText("예: 8.8.8.8")
        self.trace_no_resolve_check = QCheckBox("호스트 이름 해석 안 함 (-d / -n)")

        button_row = QHBoxLayout()
        self.tracert_button = QPushButton("tracert 실행")
        self.pathping_button = QPushButton("pathping 실행")
        self.trace_cancel_button = QPushButton("중지")
        self.trace_cancel_button.setEnabled(False)
        button_row.addWidget(self.tracert_button)
        button_row.addWidget(self.pathping_button)
        button_row.addWidget(self.trace_cancel_button)
        button_row.addStretch(1)

        form.addRow("대상", self.trace_target_edit)
        form.addRow("", self.trace_no_resolve_check)
        form.addRow("", button_row)
        layout.addWidget(group)

        self.trace_status_label = QLabel("준비")
        layout.addWidget(self.trace_status_label)
        self.trace_table = QTableWidget(0, 7)
        self.trace_table.setHorizontalHeaderLabels(["Hop", "RTT1", "RTT2", "RTT3", "평균(ms)", "목적지", "상태"])
        self._setup_table(self.trace_table)
        self._set_stretch_columns(self.trace_table, 5)
        self.trace_table.setMaximumHeight(220)
        layout.addWidget(self.trace_table)
        self.trace_output = self._output()
        layout.addWidget(self.trace_output, 1)

        self.tracert_button.clicked.connect(lambda: self.start_trace("tracert"))
        self.pathping_button.clicked.connect(lambda: self.start_trace("pathping"))
        self.trace_cancel_button.clicked.connect(self.cancel_trace)
        return page

    def start_trace(self, mode: str) -> None:
        try:
            target = validate_host_input(self.trace_target_edit.text())
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        self.trace_output.clear()
        self.trace_table.setRowCount(0)
        self.trace_row_map.clear()
        self.trace_status_label.setText(f"{mode} 실행 중...")
        self.trace_cancel_event = Event()
        self._set_trace_running(True)

        runner = self.state.trace_service.run_tracert if mode == "tracert" else self.state.trace_service.run_pathping
        self._start_worker(
            runner,
            target,
            not self.trace_no_resolve_check.isChecked(),
            cancel_event=self.trace_cancel_event,
            on_progress=self._handle_trace_progress,
            on_result=lambda result, mode=mode: self._finish_trace(mode, result),
            on_finished=lambda: self._set_trace_running(False),
            error_title=f"{mode} 실행 실패",
        )

    def _handle_trace_progress(self, line: str) -> None:
        self.trace_output.appendPlainText(line)
        hop = parse_trace_hop_line(line)
        if hop is not None:
            self._upsert_trace_hop(hop)

    def _finish_trace(self, mode: str, result: OperationResult) -> None:
        self.trace_status_label.setText(f"{mode}: {result.message}")
        if result.details and not self.trace_output.toPlainText().strip():
            self.trace_output.setPlainText(result.details)
        for hop in parse_trace_hops(result.details or self.trace_output.toPlainText()):
            self._upsert_trace_hop(hop)

    def _set_trace_running(self, running: bool) -> None:
        self.tracert_button.setEnabled(not running)
        self.pathping_button.setEnabled(not running)
        self.trace_cancel_button.setEnabled(running)

    def cancel_trace(self) -> None:
        if self.trace_cancel_event:
            self.trace_cancel_event.set()

    def _upsert_trace_hop(self, hop: TraceHop) -> None:
        row = self.trace_row_map.get(hop.hop_number)
        if row is None:
            row = self.trace_table.rowCount()
            self.trace_table.insertRow(row)
            self.trace_row_map[hop.hop_number] = row

        values = [
            str(hop.hop_number),
            hop.probe_1 or "-",
            hop.probe_2 or "-",
            hop.probe_3 or "-",
            f"{hop.average_ms:.2f}" if hop.average_ms is not None else "-",
            hop.endpoint_text,
            hop.status or "-",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 6:
                if hop.status == "정상":
                    item.setForeground(QColor("#1b5e20"))
                elif hop.status == "시간 초과":
                    item.setForeground(QColor("#b71c1c"))
                else:
                    item.setForeground(QColor("#ef6c00"))
            self.trace_table.setItem(row, column, item)
