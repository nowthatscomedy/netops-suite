from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DnsDiagnosticsMixin:
    def _build_dns_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("nslookup")
        form = QFormLayout(group)
        self.dns_query_edit = QLineEdit()
        self.dns_query_edit.setPlaceholderText("예: google.com 또는 8.8.8.8")
        self.dns_type_combo = QComboBox()
        for label, value, description in self.DNS_TYPES:
            self.dns_type_combo.addItem(label, (value, description))
        self.dns_type_hint = QLabel()
        self.dns_type_hint.setStyleSheet("color:#555;")
        self._update_dns_type_hint()
        self.dns_server_edit = QLineEdit()
        self.dns_server_edit.setPlaceholderText("예: 8.8.8.8")
        self.dns_run_button = QPushButton("조회")

        form.addRow("도메인 / IP", self.dns_query_edit)
        form.addRow("레코드 타입", self.dns_type_combo)
        form.addRow("", self.dns_type_hint)
        form.addRow("DNS 서버", self.dns_server_edit)
        form.addRow("", self.dns_run_button)
        layout.addWidget(group)

        self.dns_output = self._output()
        layout.addWidget(self.dns_output, 1)

        self.dns_type_combo.currentIndexChanged.connect(self._update_dns_type_hint)
        self.dns_run_button.clicked.connect(self.run_dns_lookup)
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
        self.dns_output.setPlainText("nslookup 실행 중...")
        self._start_worker(
            self.state.dns_service.lookup,
            query,
            record_type,
            self.dns_server_edit.text().strip(),
            on_result=lambda result: self.dns_output.setPlainText(result.details or result.message),
            error_title="nslookup 실패",
        )
