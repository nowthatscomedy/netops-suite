from __future__ import annotations

import os
from pathlib import Path
from threading import Event

import pandas as pd
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.ui.common import (
    JobRunner,
    confirm_risky_action,
    make_inline_status,
    make_step_hint,
    make_table_item,
    set_inline_status,
    set_table_minimums,
)
from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
from app.utils.file_utils import timestamped_export_path
from netops_suite.modules.inspector import (
    InspectorRunRequest,
    InspectorRunResult,
    InspectorService,
)
from netops_suite.ui.numeric_inputs import NoWheelSpinBox
from netops_suite.ui.selection_inputs import NoWheelComboBox


from netops_suite.ui.actions import ActionKind, make_action_button


class InspectorTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.runner = JobRunner(
            self.state.thread_pool, self, default_error_title="장비 점검 실패"
        )
        self.service = InspectorService(
            work_dir=self.state.paths.data_root / "inspector" / "runs",
            user_data_dir=self.state.paths.data_root / "inspector",
        )
        self.exports_dir = Path(
            getattr(
                self.state.paths,
                "exports_dir",
                self.state.paths.data_root / "exports",
            )
        )
        self._last_result: InspectorRunResult | None = None
        self._profile_dialog: InspectorProfileDialog | None = None
        self._inventory_validated = False
        self._inspector_running = False
        self._result_open_busy = False
        self._cancel_event: Event | None = None
        self._shutting_down = False
        self._build_ui()
        self._load_supported_profiles()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._inspector_running and not self._shutting_down:
            self._load_supported_profiles()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(
            make_step_hint(
                "작업 흐름: 장비 프로파일 확인/관리, 대상 장비 목록 선택, 실행 방식 선택, 검증, 실행, 결과 열기"
            )
        )
        self.inspector_splitter = QSplitter(Qt.Vertical)
        self.inspector_splitter.setChildrenCollapsible(False)

        top_panel = QWidget()
        top_panel.setObjectName("inspectorTopPanel")
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)

        profile_group = QGroupBox("1. 장비 프로파일")
        profile_group.setObjectName("inspectorProfileGroup")
        profile_layout = QVBoxLayout(profile_group)
        profile_guide = QLabel(
            "먼저 장비 제조사(vendor)/OS에 맞는 프로파일이 있는지 확인하세요."
        )
        profile_guide.setWordWrap(True)
        profile_layout.addWidget(profile_guide)
        profile_action_row = QHBoxLayout()
        self.profile_editor_button = make_action_button(
            "프로파일 관리",
            ActionKind.EDIT,
            tooltip="지원 제조사(vendor), 모델, OS별 점검 명령과 Excel 출력 컬럼을 관리합니다.",
        )
        self.profile_editor_button.clicked.connect(self._open_profile_editor)
        self.supported_toggle_button = make_action_button(
            "지원 보기", ActionKind.UTILITY
        )
        self.supported_toggle_button.setCheckable(True)
        self.supported_toggle_button.toggled.connect(
            self._set_supported_profiles_visible
        )
        profile_action_row.addWidget(self.profile_editor_button)
        profile_action_row.addWidget(self.supported_toggle_button)
        profile_action_row.addStretch(1)
        profile_layout.addLayout(profile_action_row)
        self.supported_label = QLabel("지원 제조사(vendor)/모델/OS")
        self.supported_label.setWordWrap(True)
        profile_layout.addWidget(self.supported_label)
        self.supported_table = QTableWidget(0, 8)
        self.supported_table.setHorizontalHeaderLabels(
            [
                "제조사(vendor)",
                "OS",
                "device_type",
                "명령",
                "백업",
                "파싱",
                "출력 컬럼",
                "구분",
            ]
        )
        self.supported_table.horizontalHeader().setStretchLastSection(True)
        set_table_minimums(self.supported_table, 160)
        profile_layout.addWidget(self.supported_table)
        top_layout.addWidget(profile_group)

        inventory_group = QGroupBox("2. 대상 장비 목록")
        inventory_group.setObjectName("inspectorInventoryGroup")
        inventory_layout = QFormLayout(inventory_group)
        self.inventory_path_edit = QLineEdit()
        self.inventory_path_edit.textChanged.connect(self._handle_inventory_changed)
        inventory_row = QHBoxLayout()
        inventory_row.addWidget(self.inventory_path_edit, 1)
        self.inventory_button = make_action_button(
            "선택",
            ActionKind.BROWSE,
            tooltip="점검 대상 장비 목록 Excel 파일을 선택합니다.",
        )
        self.inventory_button.clicked.connect(self._pick_inventory)
        self.sample_button = make_action_button(
            "샘플 생성",
            ActionKind.ADD,
            tooltip="필수 컬럼이 들어간 샘플 장비 목록 Excel을 만듭니다.",
        )
        self.sample_button.clicked.connect(self._create_sample_inventory)
        inventory_row.addWidget(self.inventory_button)
        inventory_row.addWidget(self.sample_button)
        inventory_layout.addRow("장비 목록 Excel", inventory_row)

        self.inventory_password_edit = QLineEdit()
        self.inventory_password_edit.setEchoMode(QLineEdit.Password)
        self.inventory_password_edit.setPlaceholderText("암호화 Excel인 경우에만 입력")
        inventory_layout.addRow("Excel 암호", self.inventory_password_edit)
        self.inventory_status_label = make_inline_status(
            "info", "대상 장비 목록 파일을 선택하거나 샘플을 생성하세요."
        )
        inventory_layout.addRow("", self.inventory_status_label)
        top_layout.addWidget(inventory_group)

        execution_group = QGroupBox("3. 실행 방식")
        execution_group.setObjectName("inspectorExecutionGroup")
        execution_layout = QFormLayout(execution_group)
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("점검", "inspection")
        self.mode_combo.addItem("백업", "backup")
        self.mode_combo.addItem("점검+백업", "inspection_backup")
        self.mode_combo.addItem("사용자 명령", "custom_commands")
        self.mode_combo.currentIndexChanged.connect(self._update_command_file_state)
        execution_layout.addRow("실행 모드", self.mode_combo)

        self.command_path_edit = QLineEdit()
        self.command_path_edit.setPlaceholderText("사용자 명령 모드에서만 필요합니다")
        self.command_path_edit.textChanged.connect(self._update_run_action_state)
        command_row = QHBoxLayout()
        command_row.addWidget(self.command_path_edit, 1)
        self.command_button = make_action_button(
            "선택",
            ActionKind.BROWSE,
            tooltip="사용자 명령 모드에서 실행할 명령 파일을 선택합니다.",
        )
        self.command_button.clicked.connect(self._pick_command_file)
        command_row.addWidget(self.command_button)
        execution_layout.addRow("사용자 명령 파일", command_row)

        self.max_workers_spin = NoWheelSpinBox()
        self.max_workers_spin.setRange(1, 128)
        self.max_workers_spin.setValue(10)
        execution_layout.addRow("동시 작업 수", self.max_workers_spin)
        self.timeout_spin = NoWheelSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(10)
        execution_layout.addRow("Timeout(초)", self.timeout_spin)
        self.retry_spin = NoWheelSpinBox()
        self.retry_spin.setRange(0, 20)
        self.retry_spin.setValue(3)
        execution_layout.addRow("재시도", self.retry_spin)
        self.output_name_edit = QLineEdit("inspection_results.xlsx")
        execution_layout.addRow("결과 파일명", self.output_name_edit)
        top_layout.addWidget(execution_group)

        validation_group = QGroupBox("4. 검증 및 실행")
        validation_group.setObjectName("inspectorValidationGroup")
        validation_layout = QVBoxLayout(validation_group)
        guide = QLabel(
            "필수 컬럼: ip, vendor, os, connection_type, port, password / "
            "선택 컬럼: username, enable_password. "
            "사용자 명령 모드는 명령 출력 원문을 장비별 원본 명령 출력(raw output) 파일과 Excel 요약으로 남깁니다."
        )
        guide.setWordWrap(True)
        validation_layout.addWidget(guide)
        self.validation_status_label = make_inline_status(
            "info", "대상 장비 목록을 선택한 뒤 먼저 검증을 권장합니다."
        )
        validation_layout.addWidget(self.validation_status_label)

        action_row = QHBoxLayout()
        self.validate_button = make_action_button(
            "먼저 검증",
            ActionKind.PRIMARY,
            tooltip="선택한 Excel의 필수 컬럼과 지원 제조사(vendor)/OS 값을 확인합니다.",
        )
        self.validate_button.clicked.connect(self._validate_inventory)
        self.run_button = make_action_button(
            "실행",
            ActionKind.START,
            tooltip="선택한 모드로 장비 점검/백업 작업을 시작합니다.",
        )
        self.run_button.clicked.connect(self._run_inspector)
        self.cancel_button = make_action_button(
            "중지",
            ActionKind.STOP,
            tooltip="실행 중인 장비 점검/백업 작업의 중지를 요청합니다.",
            object_name="inspectorCancelButton",
            enabled=False,
        )
        self.cancel_button.clicked.connect(self._cancel_inspector)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        validation_layout.addLayout(action_row)
        top_layout.addWidget(validation_group)
        top_layout.addStretch(1)

        result_group = QGroupBox("5. 결과")
        result_group.setObjectName("inspectorResultGroup")
        result_layout = QVBoxLayout(result_group)
        result_button_row = QHBoxLayout()
        self.open_result_button = make_action_button(
            "결과 Excel 열기",
            ActionKind.OPEN,
            tooltip="방금 실행에서 생성된 결과 Excel을 엽니다. 대상 장비 목록 파일은 열지 않습니다.",
        )
        self.open_result_button.clicked.connect(self._open_result)
        self.open_result_button.setEnabled(False)
        self.open_artifacts_button = make_action_button("폴더 열기", ActionKind.OPEN)
        self.open_artifacts_button.clicked.connect(self._open_artifacts)
        self.open_artifacts_button.setEnabled(False)
        self.result_log_toggle_button = make_action_button(
            "로그 보기",
            ActionKind.UTILITY,
            tooltip="검증 및 실행 상세 로그를 표시하거나 숨깁니다.",
        )
        self.result_log_toggle_button.setCheckable(True)
        self.result_log_toggle_button.toggled.connect(self._set_result_log_visible)
        result_button_row.addWidget(self.open_result_button)
        result_button_row.addWidget(self.open_artifacts_button)
        result_button_row.addWidget(self.result_log_toggle_button)
        result_button_row.addStretch(1)
        result_layout.addLayout(result_button_row)

        self.summary_label = QLabel(
            "대상 장비 목록 Excel을 선택한 뒤 검증 또는 실행하세요."
        )
        self.summary_label.setWordWrap(True)
        result_layout.addWidget(self.summary_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(140)
        self.log_view.setMaximumHeight(16777215)
        result_layout.addWidget(self.log_view, 1)
        self.top_scroll = QScrollArea()
        self.top_scroll.setObjectName("inspectorTopScrollArea")
        self.top_scroll.setWidgetResizable(True)
        self.top_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.top_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.top_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.top_scroll.viewport().setObjectName("inspectorTopScrollViewport")
        self.top_scroll.setStyleSheet(
            """
            QScrollArea#inspectorTopScrollArea,
            QWidget#inspectorTopScrollViewport,
            QWidget#inspectorTopPanel,
            QGroupBox#inspectorProfileGroup,
            QGroupBox#inspectorInventoryGroup,
            QGroupBox#inspectorExecutionGroup,
            QGroupBox#inspectorValidationGroup {
                background: #ffffff;
            }
            """
        )
        self.top_scroll.setWidget(top_panel)
        self.inspector_splitter.addWidget(self.top_scroll)
        self.inspector_splitter.addWidget(result_group)
        self.inspector_splitter.setStretchFactor(0, 3)
        self.inspector_splitter.setStretchFactor(1, 1)
        self.log_view.setVisible(False)
        self.inspector_splitter.setSizes([560, 140])
        layout.addWidget(self.inspector_splitter, 1)
        self._update_command_file_state()
        self._set_supported_profiles_visible(False)
        self._update_run_action_state()

    def _update_command_file_state(self) -> None:
        enabled = (
            self.mode_combo.currentData() == "custom_commands"
            and not self._inspector_running
        )
        self.command_path_edit.setEnabled(enabled)
        self.command_button.setEnabled(enabled)
        if enabled:
            self.command_path_edit.setPlaceholderText("사용자 명령 파일을 선택하세요")
            self._update_run_action_state()
            return
        self.command_path_edit.setPlaceholderText("사용자 명령 모드에서만 필요합니다")
        self._update_run_action_state()

    def _handle_inventory_changed(self) -> None:
        self._inventory_validated = False
        had_previous_result = self._last_result is not None
        self._last_result = None
        self._result_open_busy = False
        if hasattr(self, "open_result_button"):
            self.open_result_button.setEnabled(False)
        if hasattr(self, "open_artifacts_button"):
            self.open_artifacts_button.setEnabled(False)
        if had_previous_result and hasattr(self, "summary_label"):
            self.summary_label.setText(
                "대상 장비 목록이 변경되었습니다. 새 목록을 검증하거나 실행하세요."
            )
        path = self.inventory_path_edit.text().strip()
        if path:
            set_inline_status(
                self.inventory_status_label,
                "info",
                "대상 장비 목록이 선택되었습니다. 다음 단계: 먼저 검증",
            )
            set_inline_status(
                self.validation_status_label,
                "warning",
                "아직 대상 장비 목록을 검증하지 않았습니다.",
            )
        else:
            set_inline_status(
                self.inventory_status_label,
                "info",
                "대상 장비 목록 파일을 선택하거나 샘플을 생성하세요.",
            )
            set_inline_status(
                self.validation_status_label,
                "info",
                "대상 장비 목록을 선택한 뒤 먼저 검증을 권장합니다.",
            )
        self._update_run_action_state()

    def _update_run_action_state(self) -> None:
        if not hasattr(self, "run_button"):
            return
        has_inventory = bool(self.inventory_path_edit.text().strip())
        needs_command = self.mode_combo.currentData() == "custom_commands"
        has_command = bool(self.command_path_edit.text().strip())
        enabled = (
            has_inventory
            and (not needs_command or has_command)
            and not self._inspector_running
        )
        self.run_button.setEnabled(enabled)
        cancel_requested = bool(
            self._cancel_event is not None and self._cancel_event.is_set()
        )
        if hasattr(self, "cancel_button"):
            self.cancel_button.setEnabled(
                self._inspector_running and not cancel_requested
            )
            if cancel_requested:
                self.cancel_button.setToolTip("중지 요청을 처리하고 있습니다.")
            elif self._inspector_running:
                self.cancel_button.setToolTip(
                    "실행 중인 장비 점검/백업 작업의 중지를 요청합니다."
                )
            else:
                self.cancel_button.setToolTip(
                    "장비 점검/백업 작업을 실행하는 동안 사용할 수 있습니다."
                )
        if not has_inventory:
            self.run_button.setToolTip(
                "대상 장비 목록 Excel 파일을 선택하면 실행할 수 있습니다."
            )
        elif needs_command and not has_command:
            self.run_button.setToolTip(
                "사용자 명령 모드에서는 명령 파일을 먼저 선택하세요."
            )
        else:
            self.run_button.setToolTip(
                "선택한 모드로 장비 점검/백업 작업을 시작합니다."
            )

    def _set_supported_profiles_visible(self, visible: bool) -> None:
        self.supported_label.setVisible(visible)
        self.supported_table.setVisible(visible)
        self.supported_toggle_button.setText("지원 숨김" if visible else "지원 보기")

    def _pick_inventory(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "대상 장비 목록 선택", "", "Excel Files (*.xlsx *.xls *.xlsm)"
        )
        if path:
            self.inventory_path_edit.setText(path)

    def _pick_command_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "사용자 명령 파일 선택",
            "",
            "Command Files (*.txt *.xlsx *.xls *.xlsm)",
        )
        if path:
            self.command_path_edit.setText(path)

    def _create_sample_inventory(self) -> Path | None:
        suggested_path = timestamped_export_path(
            self.exports_dir, "sample_inventory", "xlsx"
        )
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "샘플 장비 목록 저장",
            str(suggested_path),
            "Excel Files (*.xlsx)",
        )
        if not selected_path:
            return None
        target = Path(selected_path)
        if target.suffix.casefold() != ".xlsx":
            target = (
                target.with_suffix(".xlsx")
                if target.suffix
                else Path(f"{target}.xlsx")
            )
        df = pd.DataFrame(
            [
                {
                    "ip": "192.0.2.10",
                    "vendor": "cisco",
                    "os": "ios",
                    "connection_type": "ssh",
                    "port": 22,
                    "username": "admin",
                    "password": "CHANGE_ME_PASSWORD",
                    "enable_password": "CHANGE_ME_ENABLE_PASSWORD",
                }
            ]
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            df.to_excel(target, index=False)
        except Exception as exc:
            QMessageBox.warning(
                self, "샘플 생성 실패", f"샘플 장비 목록을 저장하지 못했습니다.\n{exc}"
            )
            return None
        self.inventory_path_edit.setText(str(target))
        set_inline_status(
            self.inventory_status_label,
            "success",
            f"샘플 장비 목록이 선택되었습니다. 다음 단계: 먼저 검증 ({target})",
        )
        QMessageBox.information(
            self,
            "샘플 생성 완료",
            f"샘플 장비 목록을 저장하고 선택했습니다:\n{target}",
        )
        self.validate_button.setFocus()
        return target

    def _load_supported_profiles(self) -> None:
        try:
            profiles = self.service.supported_profile_definitions()
        except Exception as exc:
            self.supported_label.setText(self._inspector_error_message(exc))
            self._log_inspector_exception("지원 제조사(vendor) 목록 로드 실패", exc)
            return
        self.supported_label.setText(
            f"지원 제조사(vendor)/모델/OS 조합: {len(profiles)}개"
        )
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
                "참고용"
                if profile.get("is_reference")
                else ("사용자" if profile.get("is_custom") else ""),
            ]
            for column, value in enumerate(values):
                self.supported_table.setItem(row, column, make_table_item(str(value)))
        self.supported_table.resizeColumnsToContents()

    def _validate_inventory(self) -> None:
        path = self.inventory_path_edit.text().strip()
        if not path:
            set_inline_status(
                self.validation_status_label,
                "warning",
                "대상 장비 목록 Excel 파일을 먼저 선택하세요.",
            )
            return
        self._set_result_log_visible(True)
        try:
            devices = self.service.load_inventory(
                path, self.inventory_password_edit.text().strip() or None
            )
        except Exception as exc:
            self._log_inspector_exception("대상 장비 목록 검증 실패", exc)
            set_inline_status(
                self.validation_status_label,
                "error",
                self._inspector_error_message(exc),
            )
            QMessageBox.warning(self, "검증 실패", self._inspector_error_message(exc))
            return
        self._inventory_validated = True
        self.summary_label.setText(f"검증 완료: 장비 {len(devices)}대")
        set_inline_status(
            self.validation_status_label, "success", f"검증 완료: 장비 {len(devices)}대"
        )
        self.log_view.appendPlainText(
            f"[validate] {Path(path).name}: {len(devices)} devices"
        )
        self._update_run_action_state()

    def _run_inspector(self) -> None:
        if self._shutting_down or self._inspector_running:
            return
        path = self.inventory_path_edit.text().strip()
        if not path:
            set_inline_status(
                self.validation_status_label,
                "warning",
                "대상 장비 목록 Excel 파일을 먼저 선택하세요.",
            )
            return
        mode = self.mode_combo.currentData()
        if mode == "custom_commands" and not self.command_path_edit.text().strip():
            set_inline_status(
                self.validation_status_label,
                "warning",
                "사용자 명령 모드에서는 명령 파일을 먼저 선택하세요.",
            )
            return
        if not self._inventory_validated:
            set_inline_status(
                self.validation_status_label,
                "warning",
                "아직 검증하지 않았습니다. 실행 전 '먼저 검증'을 권장합니다.",
            )
        if not confirm_risky_action(
            self,
            "대량 장비 점검 실행",
            impact=(
                f"목록에 있는 장비에 SSH/Telnet 접속을 시도합니다. 최대 {self.max_workers_spin.value()}대가 동시에 처리되며 "
                "일부 장비에서 로그인 실패, 세션 잠금, 네트워크 부하가 발생할 수 있습니다."
            ),
            reversibility="기본 점검/백업 모드는 장비 설정을 변경하지 않습니다. 사용자 명령 모드는 명령 파일 내용에 따라 되돌리기 어려울 수 있습니다.",
            output_location="결과 Excel, 백업 파일, 세션 로그, 원본 명령 출력(raw output)은 설정에 지정한 결과 폴더와 inspector runs 폴더에 기록됩니다.",
            question="현재 대상 장비 목록과 실행 모드를 확인한 뒤 진행할까요?",
            confirm_text="점검/백업 실행",
        ):
            return
        request = InspectorRunRequest(
            inventory_path=path,
            mode=mode,
            inventory_password=self.inventory_password_edit.text().strip() or None,
            command_path=self.command_path_edit.text().strip() or None,
            output_name=self.output_name_edit.text().strip()
            or "inspection_results.xlsx",
            max_workers=self.max_workers_spin.value(),
            timeout=self.timeout_spin.value(),
            max_retries=self.retry_spin.value(),
        )
        self.log_view.clear()
        self.summary_label.setText("장비 점검 작업을 실행 중입니다...")
        self._last_result = None
        self._result_open_busy = False
        self._inspector_running = True
        self._cancel_event = Event()
        self._set_result_log_visible(True)
        self._set_run_controls_locked(True)
        self._update_run_action_state()
        self.open_result_button.setEnabled(False)
        self.open_artifacts_button.setEnabled(False)
        self.runner.start(
            self.service.run,
            request,
            cancel_event=self._cancel_event,
            on_progress=self._handle_progress,
            on_result=self._handle_result,
            on_finished=self._finish_inspector_run,
            on_error=self._handle_error,
        )

    def _cancel_inspector(self) -> None:
        cancel_event = self._cancel_event
        if (
            self._shutting_down
            or not self._inspector_running
            or cancel_event is None
            or cancel_event.is_set()
        ):
            return
        cancel_event.set()
        self.summary_label.setText(
            "장비 점검 작업의 중지를 요청했습니다. 진행 중인 장비 작업이 종료될 때까지 기다려 주세요."
        )
        set_inline_status(
            self.validation_status_label,
            "warning",
            "중지 요청을 보냈습니다. 현재 처리 중인 장비가 안전하게 종료되면 작업이 중지됩니다.",
        )
        self.log_view.appendPlainText(
            "[cancel] 사용자가 장비 점검 작업 중지를 요청했습니다."
        )
        self._update_run_action_state()

    def _set_result_log_visible(self, visible: bool) -> None:
        if not hasattr(self, "log_view"):
            return
        self.log_view.setVisible(visible)
        self.result_log_toggle_button.blockSignals(True)
        self.result_log_toggle_button.setChecked(visible)
        self.result_log_toggle_button.blockSignals(False)
        self.result_log_toggle_button.setText(
            "로그 숨기기" if visible else "로그 보기"
        )
        if visible:
            self.inspector_splitter.setSizes([420, 280])
        else:
            self.inspector_splitter.setSizes([560, 140])

    def _set_run_controls_locked(self, locked: bool) -> None:
        self.profile_editor_button.setEnabled(not locked)
        self.inventory_button.setEnabled(not locked)
        self.sample_button.setEnabled(not locked)
        self.validate_button.setEnabled(not locked)
        for edit in (
            self.inventory_path_edit,
            self.inventory_password_edit,
            self.command_path_edit,
            self.output_name_edit,
        ):
            edit.setReadOnly(locked)
        for widget in (
            self.mode_combo,
            self.max_workers_spin,
            self.timeout_spin,
            self.retry_spin,
        ):
            widget.setEnabled(not locked)
        self._update_command_file_state()

    def _finish_inspector_run(self) -> None:
        if self._shutting_down:
            return
        self._inspector_running = False
        self._cancel_event = None
        self._set_run_controls_locked(False)
        self._update_run_action_state()

    def _handle_progress(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        message = str(event.get("message", "") or "")
        event_type = str(event.get("type", "progress") or "progress")
        if message:
            self.log_view.appendPlainText(f"[{event_type}] {message}")

    def _handle_result(self, result: object) -> None:
        if self._shutting_down:
            return
        if not isinstance(result, InspectorRunResult):
            return
        self._last_result = result
        self._result_open_busy = False
        self.summary_label.setText(
            f"완료: 모드 {result.mode} / 장비 {result.devices_total}대 / 결과 {result.results_total}건"
        )
        set_inline_status(
            self.validation_status_label,
            "success",
            "실행이 완료되었습니다. 결과 폴더 또는 아래 버튼에서 결과를 확인하세요.",
        )
        self.open_result_button.setEnabled(bool(result.result_excel))
        self.open_artifacts_button.setEnabled(True)

    def _handle_error(self, text: str) -> None:
        if self._shutting_down:
            return
        cancel_requested = bool(
            self._cancel_event is not None and self._cancel_event.is_set()
        )
        cancelled = cancel_requested and any(
            marker in str(text) for marker in ("취소", "중지")
        )
        if cancelled:
            self.summary_label.setText("장비 점검 작업이 중지되었습니다.")
            set_inline_status(
                self.validation_status_label,
                "warning",
                "사용자 요청으로 장비 점검 작업을 중지했습니다.",
            )
            self.log_view.appendPlainText(
                "[cancelled] 장비 점검 작업이 중지되었습니다."
            )
            return
        self.summary_label.setText("장비 점검 실패")
        set_inline_status(
            self.validation_status_label, "error", self._inspector_error_message(text)
        )
        QMessageBox.warning(self, "장비 점검 실패", self._inspector_error_message(text))

    def _open_result(self) -> None:
        if (
            self._result_open_busy
            or not self._last_result
            or not self._last_result.result_excel
        ):
            return
        result_path = Path(self._last_result.result_excel)
        if not result_path.is_file():
            self.open_result_button.setEnabled(False)
            QMessageBox.warning(
                self,
                "결과 Excel 열기 실패",
                f"결과 파일을 찾을 수 없습니다.\n{result_path}",
            )
            return

        self._result_open_busy = True
        self.open_result_button.setEnabled(False)
        logger = getattr(self.state, "logger", None)
        if logger:
            logger.info(
                "Opening inspector result Excel by explicit user action: %s",
                result_path,
            )
        try:
            os.startfile(str(result_path))
        except OSError as exc:
            self._result_open_busy = False
            self.open_result_button.setEnabled(True)
            if logger:
                logger.exception(
                    "Failed to open inspector result Excel: %s", result_path
                )
            QMessageBox.warning(self, "결과 Excel 열기 실패", str(exc))
            return
        QTimer.singleShot(800, self._finish_result_open)

    def _finish_result_open(self) -> None:
        self._result_open_busy = False
        result_path = (
            Path(self._last_result.result_excel)
            if self._last_result and self._last_result.result_excel
            else None
        )
        self.open_result_button.setEnabled(
            bool(result_path and result_path.is_file() and not self._inspector_running)
        )

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._cancel_event is not None:
            self._cancel_event.set()

    def _open_artifacts(self) -> None:
        if not self._last_result:
            return
        for candidate in (
            self._last_result.backup_dir,
            self._last_result.session_log_dir,
        ):
            if candidate and Path(candidate).exists():
                os.startfile(candidate)
                return

    def _open_profile_editor(self) -> None:
        try:
            self._profile_dialog = InspectorProfileDialog(
                self.service,
                self,
                exports_dir=self.exports_dir,
            )
        except Exception as exc:
            self._log_inspector_exception("장비 프로파일 관리 열기 실패", exc)
            QMessageBox.warning(
                self, "장비 프로파일 관리 열기 실패", self._inspector_error_message(exc)
            )
            return
        self._profile_dialog.exec()
        self._load_supported_profiles()

    def _inspector_error_message(self, error: Exception | str) -> str:
        text = str(error)
        lowered = text.lower()
        dependency_markers = (
            "no module named",
            "telnetlib3 is required",
            "msoffcrypto",
            "netmiko",
            "xlrd",
        )
        if any(marker in lowered for marker in dependency_markers):
            return (
                "장비 점검에 필요한 구성요소를 불러오지 못했습니다.\n\n"
                "소스 실행이면 `python -m pip install -r requirements.txt`를 실행해 주세요.\n"
                "설치본이면 최신 설치본으로 다시 설치한 뒤 실행해 주세요."
            )
        return text

    def _log_inspector_exception(self, message: str, exc: Exception) -> None:
        logger = getattr(self.state, "logger", None)
        if logger:
            logger.exception("%s: %s", message, exc)
        else:
            self.log_view.appendPlainText(f"[error] {message}: {exc}")
