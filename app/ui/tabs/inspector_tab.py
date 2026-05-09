from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.ui.common import JobRunner
from app.ui.dialogs.inspector_vendor_template_dialog import InspectorVendorTemplateDialog
from netops_suite.modules.inspector import InspectorRunRequest, InspectorRunResult, InspectorService


class InspectorTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.runner = JobRunner(self.state.thread_pool, self, default_error_title="장비 점검 실패")
        self.service = InspectorService(
            work_dir=self.state.paths.data_root / "inspector" / "runs",
            user_data_dir=self.state.paths.data_root / "inspector",
        )
        self._last_result: InspectorRunResult | None = None
        self._template_dialog: InspectorVendorTemplateDialog | None = None
        self._build_ui()
        self._load_supported_profiles()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        input_group = QGroupBox("입력")
        input_layout = QFormLayout(input_group)
        self.inventory_path_edit = QLineEdit()
        inventory_row = QHBoxLayout()
        inventory_row.addWidget(self.inventory_path_edit, 1)
        inventory_button = QPushButton("찾기")
        inventory_button.clicked.connect(self._pick_inventory)
        sample_button = QPushButton("샘플 생성")
        sample_button.clicked.connect(self._create_sample_inventory)
        inventory_row.addWidget(inventory_button)
        inventory_row.addWidget(sample_button)
        input_layout.addRow("인벤토리 Excel", inventory_row)

        self.inventory_password_edit = QLineEdit()
        self.inventory_password_edit.setEchoMode(QLineEdit.Password)
        self.inventory_password_edit.setPlaceholderText("암호화 Excel인 경우에만 입력")
        input_layout.addRow("Excel 암호", self.inventory_password_edit)

        self.command_path_edit = QLineEdit()
        command_row = QHBoxLayout()
        command_row.addWidget(self.command_path_edit, 1)
        command_button = QPushButton("찾기")
        command_button.clicked.connect(self._pick_command_file)
        command_row.addWidget(command_button)
        input_layout.addRow("사용자 명령 파일", command_row)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("점검", "inspection")
        self.mode_combo.addItem("백업", "backup")
        self.mode_combo.addItem("점검+백업", "inspection_backup")
        self.mode_combo.addItem("사용자 명령", "custom_commands")
        input_layout.addRow("실행 모드", self.mode_combo)

        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setRange(1, 128)
        self.max_workers_spin.setValue(10)
        input_layout.addRow("동시 작업 수", self.max_workers_spin)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(10)
        input_layout.addRow("Timeout(초)", self.timeout_spin)
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 20)
        self.retry_spin.setValue(3)
        input_layout.addRow("재시도", self.retry_spin)
        self.output_name_edit = QLineEdit("inspection_results.xlsx")
        input_layout.addRow("결과 파일명", self.output_name_edit)
        layout.addWidget(input_group)

        guide = QLabel(
            "필수 컬럼: ip, vendor, os, connection_type, port, password / "
            "선택 컬럼: username, enable_password. "
            "사용자 명령 모드는 명령 출력 원문을 장비별 raw output 파일과 Excel 요약으로 남깁니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        action_row = QHBoxLayout()
        self.validate_button = QPushButton("인벤토리 검증")
        self.validate_button.clicked.connect(self._validate_inventory)
        self.run_button = QPushButton("실행")
        self.run_button.clicked.connect(self._run_inspector)
        self.template_editor_button = QPushButton("벤더/모델/OS 템플릿 편집")
        self.template_editor_button.clicked.connect(self._open_template_editor)
        self.open_result_button = QPushButton("결과 Excel 열기")
        self.open_result_button.clicked.connect(self._open_result)
        self.open_result_button.setEnabled(False)
        self.open_artifacts_button = QPushButton("산출물 폴더 열기")
        self.open_artifacts_button.clicked.connect(self._open_artifacts)
        self.open_artifacts_button.setEnabled(False)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.template_editor_button)
        action_row.addStretch(1)
        action_row.addWidget(self.open_result_button)
        action_row.addWidget(self.open_artifacts_button)
        layout.addLayout(action_row)

        self.summary_label = QLabel("Excel 인벤토리를 선택한 뒤 검증 또는 실행하세요.")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.supported_label = QLabel("지원 벤더/모델/OS")
        self.supported_label.setWordWrap(True)
        layout.addWidget(self.supported_label)
        self.supported_table = QTableWidget(0, 8)
        self.supported_table.setHorizontalHeaderLabels(
            ["벤더", "OS", "device_type", "명령", "백업", "파싱", "출력 컬럼", "custom"]
        )
        self.supported_table.horizontalHeader().setStretchLastSection(True)
        self.supported_table.setMaximumHeight(190)
        layout.addWidget(self.supported_table)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

    def _pick_inventory(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "인벤토리 선택", "", "Excel Files (*.xlsx *.xls *.xlsm)")
        if path:
            self.inventory_path_edit.setText(path)

    def _pick_command_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "사용자 명령 파일 선택", "", "Command Files (*.txt *.xlsx *.xls *.xlsm)")
        if path:
            self.command_path_edit.setText(path)

    def _create_sample_inventory(self) -> None:
        target = self.state.paths.data_root / "inspector" / "samples" / "sample_inventory.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [
                {
                    "ip": "192.0.2.10",
                    "vendor": "cisco",
                    "os": "ios",
                    "connection_type": "ssh",
                    "port": 22,
                    "username": "admin",
                    "password": "",
                    "enable_password": "",
                }
            ]
        )
        df.to_excel(target, index=False)
        self.inventory_path_edit.setText(str(target))
        QMessageBox.information(self, "샘플 생성 완료", f"샘플 인벤토리를 생성했습니다.\n{target}")

    def _load_supported_profiles(self) -> None:
        try:
            profiles = self.service.supported_profile_templates()
        except Exception as exc:
            self.supported_label.setText(f"지원 벤더 목록 로드 실패: {exc}")
            return
        self.supported_label.setText(f"지원 벤더/모델/OS 조합: {len(profiles)}개")
        self.supported_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            connection = profile.get("connection_overrides") or {}
            device_type = connection.get("default") or connection.get("ssh") or "-"
            values = [
                profile["vendor"],
                profile["os"],
                device_type,
                str(profile["command_count"]),
                profile["backup_command"] or "-",
                str(profile["parse_rule_count"]),
                ", ".join(profile["output_columns"][:8]),
                "Y" if profile.get("is_custom") else "",
            ]
            for column, value in enumerate(values):
                self.supported_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.supported_table.resizeColumnsToContents()

    def _validate_inventory(self) -> None:
        path = self.inventory_path_edit.text().strip()
        if not path:
            QMessageBox.information(self, "인벤토리 검증", "인벤토리 Excel 파일을 선택하세요.")
            return
        try:
            devices = self.service.load_inventory(path, self.inventory_password_edit.text().strip() or None)
        except Exception as exc:
            QMessageBox.warning(self, "검증 실패", str(exc))
            return
        self.summary_label.setText(f"검증 완료: 장비 {len(devices)}대")
        self.log_view.appendPlainText(f"[validate] {Path(path).name}: {len(devices)} devices")

    def _run_inspector(self) -> None:
        path = self.inventory_path_edit.text().strip()
        if not path:
            QMessageBox.information(self, "장비 점검", "인벤토리 Excel 파일을 선택하세요.")
            return
        mode = self.mode_combo.currentData()
        if mode == "custom_commands" and not self.command_path_edit.text().strip():
            QMessageBox.information(self, "장비 점검", "사용자 명령 파일을 선택하세요.")
            return
        request = InspectorRunRequest(
            inventory_path=path,
            mode=mode,
            inventory_password=self.inventory_password_edit.text().strip() or None,
            command_path=self.command_path_edit.text().strip() or None,
            output_name=self.output_name_edit.text().strip() or "inspection_results.xlsx",
            max_workers=self.max_workers_spin.value(),
            timeout=self.timeout_spin.value(),
            max_retries=self.retry_spin.value(),
        )
        self.log_view.clear()
        self.summary_label.setText("장비 점검 작업을 실행 중입니다...")
        self.run_button.setEnabled(False)
        self.open_result_button.setEnabled(False)
        self.open_artifacts_button.setEnabled(False)
        self.runner.start(
            self.service.run,
            request,
            on_progress=self._handle_progress,
            on_result=self._handle_result,
            on_finished=lambda: self.run_button.setEnabled(True),
            on_error=self._handle_error,
        )

    def _handle_progress(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        message = str(event.get("message", "") or "")
        event_type = str(event.get("type", "progress") or "progress")
        if message:
            self.log_view.appendPlainText(f"[{event_type}] {message}")

    def _handle_result(self, result: object) -> None:
        if not isinstance(result, InspectorRunResult):
            return
        self._last_result = result
        self.summary_label.setText(
            f"완료: 모드 {result.mode} / 장비 {result.devices_total}대 / 결과 {result.results_total}건"
        )
        self.open_result_button.setEnabled(bool(result.result_excel))
        self.open_artifacts_button.setEnabled(True)

    def _handle_error(self, text: str) -> None:
        self.run_button.setEnabled(True)
        self.summary_label.setText("장비 점검 실패")
        QMessageBox.warning(self, "장비 점검 실패", text)

    def _open_result(self) -> None:
        if self._last_result and self._last_result.result_excel:
            os.startfile(self._last_result.result_excel)

    def _open_artifacts(self) -> None:
        if not self._last_result:
            return
        for candidate in (self._last_result.backup_dir, self._last_result.session_log_dir):
            if candidate and Path(candidate).exists():
                os.startfile(candidate)
                return

    def _open_template_editor(self) -> None:
        self._template_dialog = InspectorVendorTemplateDialog(self.service, self)
        self._template_dialog.exec()
        self._load_supported_profiles()
