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
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.result_models import PingResult
from app.ui.common import make_empty_state, make_inline_status, set_inline_status
from app.utils.parser import parse_target_entries
from app.utils.validators import ValidationError


from netops_suite.ui.actions import ActionKind, make_action_button

DEFAULT_PING_COUNT = 4
DEFAULT_PING_TIMEOUT_MS = 4000


class PingDiagnosticsMixin:
    def _build_ping_tab(self) -> QWidget:
        page = QScrollArea()
        page.setObjectName("pingScrollArea")
        page.setWidgetResizable(True)
        page.setFrameShape(QScrollArea.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        page.setStyleSheet(
            "QScrollArea#pingScrollArea { background:#ffffff; border:0; }"
        )
        page.viewport().setStyleSheet("background:#ffffff;")
        content = QWidget()
        content.setObjectName("pingPageContent")
        content.setStyleSheet("QWidget#pingPageContent { background:#ffffff; }")
        layout = QVBoxLayout(content)
        self.ping_scroll_area = page
        self.ping_page_content = content

        group = QGroupBox("멀티 Ping")
        self.ping_input_group = group
        group_layout = QGridLayout(group)
        group_layout.setColumnStretch(1, 1)
        group_layout.setHorizontalSpacing(10)
        group_layout.setVerticalSpacing(6)
        self.ping_targets_edit = QPlainTextEdit()
        self.ping_targets_edit.setTabChangesFocus(True)
        self.ping_targets_edit.setAccessibleName("Ping 대상 목록")
        target_height = self.ping_targets_edit.fontMetrics().lineSpacing() * 4 + 24
        self.ping_targets_edit.setMinimumHeight(target_height)
        self.ping_targets_edit.setMaximumHeight(target_height + self.ping_targets_edit.fontMetrics().lineSpacing())
        self.ping_targets_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.ping_targets_edit.setPlaceholderText("GW,192.168.0.1\nDNS,8.8.8.8\n192.168.0.254")
        self.ping_targets_edit.setToolTip(
            "한 줄에 하나씩 입력합니다. 형식: 이름,IP 또는 IP. "
            "이름은 결과표의 이름 열에 표시되며 입력한 모든 대상을 실행합니다."
        )
        self.ping_targets_help_label = QLabel(
            "한 줄에 하나씩 입력: 이름,IP 또는 IP "
            "(이름을 생략하면 대상 주소가 이름으로 사용되며, 입력한 모든 대상을 실행합니다)"
        )
        self.ping_targets_help_label.setObjectName("pingTargetsHelpLabel")
        self.ping_targets_help_label.setWordWrap(False)
        self.ping_targets_help_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ping_targets_help_label.setStyleSheet("color:#667085; padding:2px 2px 0 2px;")
        targets_layout = QVBoxLayout()
        targets_layout.setContentsMargins(0, 0, 0, 0)
        targets_layout.setSpacing(4)
        targets_layout.addWidget(self.ping_targets_edit)
        targets_layout.addWidget(self.ping_targets_help_label)
        self.ping_count_edit = QLineEdit()
        self.ping_count_edit.setAccessibleName("Ping 횟수")
        self.ping_count_edit.setPlaceholderText(str(DEFAULT_PING_COUNT))
        self.ping_count_edit.setMaximumWidth(110)
        self.ping_timeout_edit = QLineEdit()
        self.ping_timeout_edit.setAccessibleName("Ping 제한 시간 밀리초")
        self.ping_timeout_edit.setPlaceholderText(str(DEFAULT_PING_TIMEOUT_MS))
        self.ping_timeout_edit.setMaximumWidth(110)
        self.ping_continuous_check = QCheckBox("계속 실행 (-t)")
        self.ping_continuous_hint = make_inline_status("warning", "")

        options_row = QHBoxLayout()
        options_row.setSpacing(8)
        options_row.addWidget(QLabel("횟수"))
        options_row.addWidget(self.ping_count_edit)
        options_row.addWidget(QLabel("Timeout (ms)"))
        options_row.addWidget(self.ping_timeout_edit)
        options_row.addStretch(1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self.ping_continuous_check)
        self.ping_start_button = make_action_button("실행", ActionKind.START, tooltip="입력한 대상에 Ping을 실행합니다.")
        self.ping_cancel_button = make_action_button("중지", ActionKind.STOP)
        self.ping_cancel_button.setEnabled(False)
        button_row.addWidget(self.ping_start_button)
        button_row.addWidget(self.ping_cancel_button)
        button_row.addStretch(1)

        group_layout.addWidget(QLabel("대상 목록"), 0, 0, 2, 1, alignment=Qt.AlignmentFlag.AlignTop)
        group_layout.addLayout(targets_layout, 0, 1, 2, 1)
        group_layout.addWidget(QLabel("실행 조건"), 2, 0)
        group_layout.addLayout(options_row, 2, 1)
        group_layout.addLayout(button_row, 3, 1)
        group_layout.addWidget(self.ping_continuous_hint, 4, 1)
        layout.addWidget(group)

        self.ping_status_label = make_inline_status("info", "")
        self.ping_status_label.setAccessibleName("Ping 작업 상태")
        layout.addWidget(self.ping_status_label)

        self.ping_table = QTableWidget(0, 11)
        self.ping_table.setHorizontalHeaderLabels(
            ["이름", "대상", "상태", "전송", "수신", "실패", "손실률", "최소(ms)", "평균(ms)", "최대(ms)", "최근 시각"]
        )
        self._setup_table(self.ping_table)
        # Keep the address fully identifiable on the supported 1024 px layout.
        # The name may use remaining space; overflowing metric columns can be
        # reached with the table's horizontal scrollbar.
        self._set_stretch_columns(self.ping_table, 0, minimum_section_size=62)
        self.ping_table.setSortingEnabled(True)
        self.ping_empty_label = make_empty_state("대상을 입력하고 실행을 누르면 Ping 결과가 표시됩니다.")

        self.ping_log = self._output()
        self.ping_log_panel = self._build_log_panel("실시간 로그", self.ping_log)
        self.ping_splitter = self._build_result_splitter(
            key="ping",
            table=self.ping_table,
            log_panel=self.ping_log_panel,
        )
        layout.addWidget(self.ping_empty_label)
        layout.addWidget(self.ping_splitter, 1)

        self.ping_start_button.clicked.connect(self.start_ping)
        self.ping_cancel_button.clicked.connect(self.cancel_ping)
        self.ping_continuous_check.toggled.connect(self._toggle_ping_continuous)
        page.setWidget(content)
        return page

    def _toggle_ping_continuous(self, checked: bool) -> None:
        self.ping_count_edit.setEnabled(not checked)
        set_inline_status(
            self.ping_continuous_hint,
            "warning",
            "중지를 누를 때까지 Ping이 계속 실행됩니다." if checked else "",
        )

    def start_ping(self) -> None:
        try:
            targets = parse_target_entries(self.ping_targets_edit.toPlainText())
            if not targets:
                raise ValidationError("최소 1개 이상의 Ping 대상을 입력해 주세요.")
            count = self._positive_int_or_default(
                self.ping_count_edit,
                "Ping 횟수",
                DEFAULT_PING_COUNT,
            )
            timeout_ms = self._positive_int_or_default(
                self.ping_timeout_edit,
                "Ping Timeout",
                DEFAULT_PING_TIMEOUT_MS,
            )
        except ValidationError as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return

        self.ping_results = []
        self.ping_row_map.clear()
        self.ping_log_lines.clear()
        self.ping_table.setRowCount(0)
        self.ping_empty_label.setVisible(False)
        self.ping_log.clear()
        self.ping_empty_label.setText(
            "대상을 입력하고 실행을 누르면 Ping 결과가 표시됩니다."
        )
        self.ping_cancel_event = Event()
        self._ping_total_targets = len(targets)
        self._ping_had_error = False
        self._set_ping_running(True)
        self._update_ping_status("info", completed=0)

        self._start_worker(
            self.state.ping_service.run_multi_ping,
            self.ping_targets_edit.toPlainText(),
            count,
            timeout_ms,
            continuous=self.ping_continuous_check.isChecked(),
            cancel_event=self.ping_cancel_event,
            on_progress=self._handle_ping_progress,
            on_result=self._finish_ping,
            on_finished=lambda: self._set_ping_running(False),
            on_error=self._handle_ping_error,
        )

    def _handle_ping_progress(self, event: dict) -> None:
        result: PingResult = event["result"]
        line = str(event.get("line", "") or "")
        key = (result.name, result.target)
        if line:
            self.ping_log.appendPlainText(line)
            self.ping_log_lines.setdefault(key, []).append(line)

        sort_state = self._capture_sort_state(self.ping_table)
        if sort_state[0]:
            self.ping_table.setSortingEnabled(False)

        row = self._find_ping_row(key)
        if row is None:
            row = self.ping_table.rowCount()
            self.ping_table.insertRow(row)
            self.ping_row_map[key] = row
            self.ping_empty_label.setVisible(False)

        values = [
            result.name,
            result.target,
            result.status,
            str(result.sent),
            str(result.received),
            str(max(result.sent - result.received, 0)),
            f"{result.packet_loss:.0f}%",
            f"{result.min_rtt:.1f}" if result.min_rtt is not None else "-",
            f"{result.avg_rtt:.1f}" if result.avg_rtt is not None else "-",
            f"{result.max_rtt:.1f}" if result.max_rtt is not None else "-",
            result.last_seen or "-",
        ]
        sort_values = [
            result.name.casefold(),
            result.target.casefold(),
            result.status.casefold(),
            result.sent,
            result.received,
            max(result.sent - result.received, 0),
            result.packet_loss,
            self._nullable_number_sort_value(result.min_rtt),
            self._nullable_number_sort_value(result.avg_rtt),
            self._nullable_number_sort_value(result.max_rtt),
            result.last_seen or "",
        ]
        for column, value in enumerate(values):
            item = self._sortable_table_item(value, sort_values[column])
            item.setToolTip(value)
            if column == 2:
                if result.status == "정상":
                    item.setForeground(QColor("#1b5e20"))
                elif result.status in ("일부 손실", "시간 초과"):
                    item.setForeground(QColor("#ef6c00"))
                else:
                    item.setForeground(QColor("#b71c1c"))
            self.ping_table.setItem(row, column, item)
        self.ping_row_map[key] = row
        self._restore_sort_state(self.ping_table, sort_state)
        self._rebuild_ping_row_map()
        self._update_ping_status("info")

    def _finish_ping(self, results: list[PingResult]) -> None:
        self.ping_results = results
        for result in results:
            self._handle_ping_progress({"result": result})
        self.ping_empty_label.setVisible(not bool(results))
        if self.ping_cancel_event is not None and self.ping_cancel_event.is_set():
            self._update_ping_status("warning", final_prefix="중지됨")
        else:
            self._update_ping_status("success", final_prefix="완료")

    def _set_ping_running(self, running: bool) -> None:
        self.ping_start_button.setEnabled(not running)
        self.ping_cancel_button.setEnabled(running)
        self.ping_targets_edit.setEnabled(not running)
        self.ping_count_edit.setEnabled(
            not running and not self.ping_continuous_check.isChecked()
        )
        self.ping_timeout_edit.setEnabled(not running)
        self.ping_continuous_check.setEnabled(not running)

    def cancel_ping(self) -> None:
        if self.ping_cancel_event:
            self.ping_cancel_event.set()
            self.ping_cancel_button.setEnabled(False)
            self._update_ping_status("warning", final_prefix="중지 요청")

    def _handle_ping_error(self, message: str) -> None:
        self._ping_had_error = True
        detail = str(message).strip() or "Ping 실행 중 오류가 발생했습니다."
        self.ping_empty_label.setText(
            "Ping을 완료하지 못했습니다. 대상과 네트워크 상태를 확인한 뒤 다시 시도해 주세요."
        )
        self.ping_empty_label.show()
        set_inline_status(
            self.ping_status_label,
            "error",
            f"Ping 실행 실패: {detail}",
        )

    def _update_ping_status(
        self,
        kind: str,
        *,
        completed: int | None = None,
        final_prefix: str = "실행 중",
    ) -> None:
        total = int(getattr(self, "_ping_total_targets", 0) or 0)
        completed_count = (
            len(self.ping_row_map) if completed is None else int(completed)
        )
        normal_count = 0
        for row in range(self.ping_table.rowCount()):
            if self._cell(self.ping_table, row, 2) == "정상":
                normal_count += 1
        problem_count = max(completed_count - normal_count, 0)
        set_inline_status(
            self.ping_status_label,
            kind,
            (
                f"{final_prefix} · 결과 {completed_count}/{total}"
                f" · 정상 {normal_count} · 확인 필요 {problem_count}"
            ),
        )

    def _find_ping_row(self, key: tuple[str, str]) -> int | None:
        mapped_row = self.ping_row_map.get(key)
        if mapped_row is not None and self._ping_row_matches(mapped_row, key):
            return mapped_row
        for row in range(self.ping_table.rowCount()):
            if self._ping_row_matches(row, key):
                return row
        return None

    def _ping_row_matches(self, row: int, key: tuple[str, str]) -> bool:
        if row < 0 or row >= self.ping_table.rowCount():
            return False
        return self._cell(self.ping_table, row, 0) == key[0] and self._cell(self.ping_table, row, 1) == key[1]

    def _rebuild_ping_row_map(self) -> None:
        self.ping_row_map.clear()
        for row in range(self.ping_table.rowCount()):
            name = self._cell(self.ping_table, row, 0)
            target = self._cell(self.ping_table, row, 1)
            if name or target:
                self.ping_row_map[(name, target)] = row
