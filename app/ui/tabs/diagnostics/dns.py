from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.common import make_empty_state, make_inline_status, set_inline_status


from netops_suite.ui.actions import ActionKind, make_action_button
from netops_suite.ui.selection_inputs import NoWheelComboBox

class DnsDiagnosticsMixin:
    def _build_dns_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("DNS 조회 (nslookup)")
        form = QFormLayout(group)
        self.dns_query_edit = QLineEdit()
        self.dns_query_edit.setPlaceholderText("예: google.com 또는 8.8.8.8")
        self.dns_query_edit.setAccessibleName("DNS 조회 도메인 또는 IP")
        self.dns_type_combo = NoWheelComboBox()
        for label, value, description in self.DNS_TYPES:
            self.dns_type_combo.addItem(label, (value, description))
        self.dns_type_hint = QLabel()
        self.dns_type_hint.setStyleSheet("color:#555;")
        self._update_dns_type_hint()
        self.dns_server_edit = QLineEdit()
        self.dns_server_edit.setPlaceholderText("예: 8.8.8.8")
        self.dns_server_edit.setAccessibleName("DNS 서버")
        self.dns_run_button = make_action_button("조회", ActionKind.PRIMARY, tooltip="DNS 레코드를 조회합니다.")
        self.dns_export_button = make_action_button("TXT 저장", ActionKind.EXPORT, enabled=False)
        button_row = QHBoxLayout()
        button_row.addWidget(self.dns_run_button)
        button_row.addWidget(self.dns_export_button)
        button_row.addStretch(1)

        form.addRow("도메인 / IP", self.dns_query_edit)
        form.addRow("레코드 타입", self.dns_type_combo)
        form.addRow("", self.dns_type_hint)
        form.addRow("DNS 서버", self.dns_server_edit)
        form.addRow("", button_row)
        layout.addWidget(group)

        self.dns_status_label = make_inline_status("info", "")
        layout.addWidget(self.dns_status_label)
        self.dns_empty_label = make_empty_state("도메인 또는 IP를 입력하고 조회를 누르면 결과가 표시됩니다.")
        layout.addWidget(self.dns_empty_label)
        self.dns_output = self._output()
        self.dns_output.setPlaceholderText("DNS 조회 결과가 여기에 표시됩니다.")
        layout.addWidget(self.dns_output, 1)

        self.dns_type_combo.currentIndexChanged.connect(self._update_dns_type_hint)
        self.dns_run_button.clicked.connect(self.run_dns_lookup)
        self.dns_export_button.clicked.connect(self.export_dns_result)
        return page

    def _update_dns_type_hint(self) -> None:
        _value, description = self.dns_type_combo.currentData()
        self.dns_type_hint.setText(description)

    def run_dns_lookup(self) -> None:
        query = self.dns_query_edit.text().strip()
        if not query:
            QMessageBox.warning(self, "입력 확인", "도메인 또는 IP를 입력해 주세요.")
            return

        record_type, _description = self.dns_type_combo.currentData()
        self.dns_empty_label.hide()
        self.dns_empty_label.setText(
            "도메인 또는 IP를 입력하고 조회를 누르면 결과가 표시됩니다."
        )
        self.dns_output.clear()
        self.dns_export_button.setEnabled(False)
        self._set_dns_running(True)
        set_inline_status(self.dns_status_label, "info", "DNS 조회(nslookup)를 실행 중입니다...")
        self._start_worker(
            self.state.dns_service.lookup,
            query,
            record_type,
            self.dns_server_edit.text().strip(),
            on_result=self._finish_dns_lookup,
            on_finished=lambda: self._set_dns_running(False),
            on_error=self._handle_dns_error,
        )

    def _finish_dns_lookup(self, result) -> None:
        text = result.details or result.message
        self.dns_output.setPlainText(text)
        self.dns_export_button.setEnabled(bool(text.strip()))
        kind = "success" if getattr(result, "success", False) else "error"
        set_inline_status(self.dns_status_label, kind, result.message)
        self.dns_empty_label.setVisible(not bool(text.strip()))

    def _handle_dns_error(self, message: str) -> None:
        detail = str(message).strip() or "DNS 조회 중 오류가 발생했습니다."
        self.dns_output.clear()
        self.dns_export_button.setEnabled(False)
        self.dns_empty_label.setText(
            "DNS 조회를 완료하지 못했습니다. 입력과 네트워크 상태를 확인한 뒤 다시 시도해 주세요."
        )
        self.dns_empty_label.show()
        set_inline_status(self.dns_status_label, "error", f"DNS 조회 실패: {detail}")

    def _set_dns_running(self, running: bool) -> None:
        self.dns_run_button.setEnabled(not running)
        self.dns_query_edit.setEnabled(not running)
        self.dns_type_combo.setEnabled(not running)
        self.dns_server_edit.setEnabled(not running)

    def export_dns_result(self) -> None:
        text = self.dns_output.toPlainText().strip()
        if not text:
            set_inline_status(self.dns_status_label, "warning", "저장할 DNS 조회 결과가 없습니다.")
            return
        path = self._choose_export_path(
            "dns_lookup",
            "txt",
            "DNS 조회 결과 저장",
            "텍스트 파일 (*.txt)",
        )
        if path is None:
            return
        try:
            path.write_text(text + "\n", encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            set_inline_status(
                self.dns_status_label,
                "error",
                f"DNS 조회 결과를 저장하지 못했습니다: {path} ({exc})",
            )
            return
        set_inline_status(self.dns_status_label, "success", f"DNS 조회 결과를 저장했습니다: {path}")
