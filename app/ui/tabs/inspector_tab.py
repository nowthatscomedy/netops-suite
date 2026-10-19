from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event

import pandas as pd
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QCheckBox,
    QComboBox,
    QAbstractItemView,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
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
    make_table_item,
    make_visible_checkbox,
    set_inline_status,
    set_table_minimums,
)
from app.ui.common.required import mark_missing, required_label
from app.ui.dialogs.backup_diff_dialog import BackupDiffDialog
from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
from app.ui.dialogs.inventory_editor_dialog import InventoryEditorDialog
from app.ui.common.disclosure import (
    CollapsibleSection,
    add_page_header_action,
    make_page_header,
)
from app.utils.file_utils import timestamped_export_path
from netops_suite.modules.inspector import (
    CustomCommandValidationSummary,
    InspectorRunRequest,
    InspectorRunResult,
    InspectorService,
)
from netops_suite.modules.inspector.backup_compare import (
    BackupComparison,
    backup_file_for_result,
    compare_backup_file,
)
from netops_suite.modules.inspector.service import DeviceCommandValidationSummary
from netops_suite.modules.inspector.sample_inventory import (
    SAMPLE_BASE_COLUMNS,
    build_sample_inventory_rows,
    sample_connection_type,
)
from netops_suite.ui.numeric_inputs import NoWheelSpinBox
from netops_suite.ui.selection_inputs import NoWheelComboBox
from netops_suite.ui.icons import icon


from netops_suite.ui.actions import ActionKind, make_action_button


_CONFIRM_PREVIEW_LIMIT = 8
_FAILED_PREVIEW_LIMIT = 5

# (mode key, button label, what the mode does)
MODE_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("inspection", "점검", "점검 프로파일의 명령을 실행하고 출력값을 결과 Excel 열로 정리합니다."),
    ("backup", "백업", "장비 설정을 파일로 저장하고, 이전 백업과 달라진 장비를 알려 줍니다."),
    ("inspection_backup", "점검+백업", "한 번 접속해 점검과 백업을 함께 실행합니다."),
    ("custom_commands", "명령 일괄 입력", "직접 입력한 명령을 목록의 모든 장비에 차례로 실행합니다."),
)
MODE_LABELS = {key: label for key, label, _description in MODE_CHOICES}

_MODE_BUTTON_STYLE = """
QPushButton#inspectorModeButton {
    background: #ffffff; color: #1e293b; border: 1px solid #cbd5e1;
    border-radius: 6px; padding: 6px 14px; font-weight: 600;
}
QPushButton#inspectorModeButton:hover { background: #f1f5f9; }
QPushButton#inspectorModeButton:checked {
    background: #e7effc; color: #1d4ed8; border: 1px solid #2563eb;
}
QPushButton#inspectorModeButton:disabled { color: #94a3b8; }
"""


class InspectorTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.runner = JobRunner(
            self.state.thread_pool, self, default_error_title="장비 점검 실패"
        )
        self.exports_dir = self._current_exports_dir()
        # Inspection results, backups and session logs share the results folder.
        self.service = InspectorService(
            work_dir=self.exports_dir,
            user_data_dir=self.state.paths.data_root / "inspector",
        )
        paths_changed = getattr(self.state, "paths_changed", None)
        if paths_changed is not None:
            paths_changed.connect(self._sync_exports_dir)
        self._last_result: InspectorRunResult | None = None
        self._profile_dialog: InspectorProfileDialog | None = None
        self._inventory_validated = False
        self._custom_command_validation: CustomCommandValidationSummary | None = None
        self._inspector_running = False
        self._result_open_busy = False
        self._cancel_event: Event | None = None
        self._shutting_down = False
        self._last_request: InspectorRunRequest | None = None
        self._run_progress = {"total": 0, "done": 0, "success": 0, "failed": 0}
        self._backup_comparisons: dict[str, BackupComparison] = {}
        self._result_rows: list[dict] = []
        self._device_commands: dict[str, list[str]] = {}
        self._device_commands_source = ""
        self._missing_profile: tuple[str, str] | None = None
        self._build_ui()
        self._load_supported_profiles()

    def _current_exports_dir(self) -> Path:
        paths = self.state.paths
        return Path(getattr(paths, "exports_dir", Path(paths.data_root) / "results"))

    def _sync_exports_dir(self) -> None:
        self.exports_dir = self._current_exports_dir()
        self.service.work_dir = self.exports_dir

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._inspector_running and not self._shutting_down:
            self._load_supported_profiles()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        page_header = make_page_header("장비 점검·백업", "장비 목록을 준비하고 검증한 뒤 실행하세요.")
        self.profile_editor_button = make_action_button(
            "점검 프로파일 만들기·관리",
            ActionKind.PRIMARY,
            tooltip=(
                "점검 프로파일은 장비에 실행할 명령과 출력에서 결과 Excel에 넣을 값을 정합니다. "
                "설정 명령 만들기의 '설정 프로파일'과는 다릅니다."
            ),
            object_name="inspectorProfileEditorButton",
        )
        self.profile_editor_button.clicked.connect(lambda: self._open_profile_editor())
        self.profile_editor_button.setIcon(icon("server-cog", "#ffffff"))
        add_page_header_action(page_header, self.profile_editor_button)
        layout.addWidget(page_header)
        self.inspector_splitter = QSplitter(Qt.Vertical)
        self.inspector_splitter.setChildrenCollapsible(False)

        top_panel = QWidget()
        top_panel.setObjectName("inspectorTopPanel")
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.profile_section = CollapsibleSection("지원 장비 목록")
        profile_group = QGroupBox()
        profile_group.setObjectName("inspectorProfileGroup")
        profile_layout = QVBoxLayout(profile_group)
        self.supported_toggle_button = self.profile_section.toggle_button
        self.supported_toggle_button.toggled.connect(
            self._set_supported_profiles_visible
        )
        self.supported_label = QLabel("지원 제조사(vendor)/모델/OS")
        self.supported_label.setWordWrap(True)
        profile_layout.addWidget(self.supported_label)
        self.supported_table = QTableWidget(0, 9)
        self.supported_table.setHorizontalHeaderLabels(
            [
                "제조사(vendor)",
                "OS",
                "장비 모델",
                "device_type",
                "명령",
                "백업",
                "파싱",
                "출력 컬럼",
                "구분",
            ]
        )
        self.supported_table.horizontalHeader().setStretchLastSection(True)
        self.supported_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.supported_table.itemSelectionChanged.connect(
            self._sync_sample_profile_from_table
        )
        set_table_minimums(self.supported_table, 160)
        profile_layout.addWidget(self.supported_table)
        self.profile_section.content_layout.addWidget(profile_group)

        inventory_group = QGroupBox("2. 대상 장비 목록")
        inventory_group.setObjectName("inspectorInventoryGroup")
        inventory_layout = QVBoxLayout(inventory_group)
        self.inventory_path_edit = QLineEdit()
        self.inventory_path_edit.textChanged.connect(self._handle_inventory_changed)
        inventory_row = QHBoxLayout()
        # The label sits above the row so the path lines up with the controls below.
        self.inventory_path_label = required_label("장비 목록 Excel")
        self.inventory_path_label.setBuddy(self.inventory_path_edit)
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
            tooltip=(
                "선택한 장비 종류와 실행할 작업에 맞는 열과 예시 값으로 "
                "장비 목록 Excel을 만듭니다."
            ),
        )
        self.sample_button.clicked.connect(self._create_sample_inventory)
        self.inventory_edit_button = make_action_button(
            "표에서 만들기",
            ActionKind.EDIT,
            tooltip="Excel을 열지 않고 이 프로그램 안의 표에서 장비 목록을 만들거나 고칩니다.",
            object_name="inspectorInventoryEditButton",
        )
        self.inventory_edit_button.clicked.connect(self._open_inventory_editor)
        inventory_row.addWidget(self.inventory_button)
        inventory_row.addWidget(self.inventory_edit_button)
        inventory_layout.addWidget(self.inventory_path_label)
        inventory_layout.addLayout(inventory_row)

        sample_row = QHBoxLayout()
        self.sample_profile_label = QLabel("샘플 장비 종류")
        self.sample_profile_combo = NoWheelComboBox()
        self.sample_profile_combo.setObjectName("inspectorSampleProfileCombo")
        # Long vendor/OS/model names must not widen the workspace on small windows.
        self.sample_profile_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.sample_profile_combo.setMinimumContentsLength(14)
        self.sample_profile_combo.setAccessibleName("샘플 장비 목록에 넣을 장비 종류")
        self.sample_profile_combo.setToolTip(
            "샘플 Excel에 들어갈 제조사·OS·모델입니다. "
            "지원 장비 목록에서 행을 고르면 함께 바뀝니다."
        )
        self.sample_profile_label.setBuddy(self.sample_profile_combo)
        sample_row.addWidget(self.sample_profile_label)
        sample_row.addWidget(self.sample_profile_combo, 1)
        sample_row.addWidget(self.sample_button)
        inventory_layout.addLayout(sample_row)

        self.inventory_password_edit = QLineEdit()
        self.inventory_password_edit.setEchoMode(QLineEdit.Password)
        self.inventory_password_edit.setPlaceholderText("암호화 Excel인 경우에만 입력")
        self.inventory_password_edit.textChanged.connect(
            self._handle_inventory_password_changed
        )
        self.inventory_password_check = QCheckBox("암호가 있는 Excel")
        option_row = QHBoxLayout()
        option_row.setSpacing(18)
        option_row.addWidget(self.inventory_password_check)
        inventory_layout.addLayout(option_row)
        self.inventory_password_fields = QWidget()
        password_layout = QFormLayout(self.inventory_password_fields)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.addRow(required_label("Excel 암호"), self.inventory_password_edit)
        inventory_layout.addWidget(self.inventory_password_fields)
        self.inventory_password_fields.hide()
        self.inventory_password_check.toggled.connect(self._toggle_inventory_password)

        self.common_account_check = QCheckBox("공통 계정 사용 (Excel의 빈 계정·비밀번호 칸에 적용)")
        option_row.addWidget(self.common_account_check)
        option_row.addStretch(1)
        self.common_account_check.setObjectName("inspectorCommonAccountCheck")
        self.common_account_check.setToolTip(
            "모든 장비가 같은 계정을 쓰면 여기에 한 번만 입력하세요. "
            "Excel에 값이 있는 칸은 그대로 쓰고, 입력한 값은 저장하지 않습니다."
        )
        self.common_account_fields = QWidget()
        self.common_account_fields.setObjectName("inspectorCommonAccountFields")
        common_layout = QFormLayout(self.common_account_fields)
        common_layout.setContentsMargins(0, 0, 0, 0)
        self.common_username_edit = QLineEdit()
        self.common_username_edit.setPlaceholderText("예: admin (필요한 장비만)")
        self.common_password_edit = QLineEdit()
        self.common_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.common_enable_edit = QLineEdit()
        self.common_enable_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.common_enable_edit.setPlaceholderText("필요한 장비만 입력")
        common_layout.addRow("계정", self.common_username_edit)
        common_layout.addRow(required_label("비밀번호"), self.common_password_edit)
        common_layout.addRow("enable 비밀번호", self.common_enable_edit)
        inventory_layout.addWidget(self.common_account_fields)
        self.common_account_fields.hide()
        self.common_account_check.toggled.connect(self._toggle_common_account)
        for edit in (
            self.common_username_edit,
            self.common_password_edit,
            self.common_enable_edit,
        ):
            edit.textChanged.connect(self._handle_common_account_changed)
        self.inventory_status_label = make_inline_status(
            "info", "대상 장비 목록 파일을 선택하거나 샘플을 생성하세요."
        )
        inventory_layout.addWidget(self.inventory_status_label)

        execution_group = QGroupBox("1. 실행할 작업")
        execution_group.setObjectName("inspectorExecutionGroup")
        execution_layout = QFormLayout(execution_group)
        # The hidden combo holds the selected mode; the buttons are what people click.
        self.mode_combo = NoWheelComboBox(self)
        for key, label, _description in MODE_CHOICES:
            self.mode_combo.addItem(label, key)
        self.mode_combo.hide()
        self.mode_selector = QWidget()
        self.mode_selector.setObjectName("inspectorModeSelector")
        mode_row = QHBoxLayout(self.mode_selector)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self.mode_button_group = QButtonGroup(self)
        self.mode_button_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for index, (key, label, description) in enumerate(MODE_CHOICES):
            button = make_action_button(
                label, ActionKind.SECONDARY, tooltip=description, object_name="inspectorModeButton"
            )
            button.setIcon(QIcon())
            button.setStyleSheet(_MODE_BUTTON_STYLE)
            button.setCheckable(True)
            button.setAccessibleName(f"실행 모드 {label}")
            self.mode_button_group.addButton(button, index)
            self.mode_buttons[key] = button
            mode_row.addWidget(button)
        self.mode_description_label = QLabel(MODE_CHOICES[0][2])
        self.mode_description_label.setObjectName("inspectorModeDescription")
        self.mode_description_label.setWordWrap(True)
        self.mode_description_label.setStyleSheet("color:#475569; padding-left:6px;")
        mode_row.addWidget(self.mode_description_label, 1)
        self.mode_buttons["inspection"].setChecked(True)
        self.mode_button_group.idClicked.connect(self.mode_combo.setCurrentIndex)
        self.mode_combo.currentIndexChanged.connect(self._handle_mode_changed)
        execution_layout.addRow("실행 모드", self.mode_selector)

        self.command_fields = QWidget()
        self.command_fields.setObjectName("inspectorCustomCommandFields")
        command_layout = QFormLayout(self.command_fields)
        command_layout.setContentsMargins(0, 0, 0, 0)

        source_row = QHBoxLayout()
        self.command_inline_radio = QRadioButton("직접 입력")
        self.command_inline_radio.setToolTip("명령을 이 화면에 한 줄에 하나씩 입력합니다.")
        self.command_file_radio = QRadioButton("명령 파일")
        self.command_file_radio.setToolTip("미리 만든 .txt 또는 Excel 명령 파일을 사용합니다.")
        self.command_device_radio = QRadioButton("설정 명령 만들기에서 받은 장비별 CLI")
        self.command_device_radio.setObjectName("inspectorDeviceCommandRadio")
        self.command_device_radio.setToolTip(
            "설정 명령 만들기에서 보낸 장비별 CLI를 장비 목록의 같은 IP 장비에 실행합니다."
        )
        self.command_device_radio.hide()
        self.command_source_group = QButtonGroup(self)
        self.command_source_group.addButton(self.command_inline_radio)
        self.command_source_group.addButton(self.command_file_radio)
        self.command_source_group.addButton(self.command_device_radio)
        self.command_inline_radio.setChecked(True)
        self.command_source_group.buttonToggled.connect(
            lambda _button, checked: self._handle_command_source_changed(checked)
        )
        source_row.addWidget(self.command_inline_radio)
        source_row.addWidget(self.command_file_radio)
        source_row.addWidget(self.command_device_radio)
        source_row.addStretch(1)
        command_layout.addRow("명령 입력 방식", source_row)

        self.command_text_edit = QPlainTextEdit()
        self.command_text_edit.setObjectName("inspectorCustomCommandEditor")
        self.command_text_edit.setAccessibleName("실행할 명령")
        self.command_text_edit.setPlaceholderText(
            "한 줄에 명령 하나씩 입력합니다.\n"
            "show version\n"
            "show interface {{ interface }}"
        )
        self.command_text_edit.setTabChangesFocus(True)
        self.command_text_edit.setFixedHeight(104)
        self.command_text_edit.textChanged.connect(self._handle_command_changed)
        command_layout.addRow(required_label("실행할 명령"), self.command_text_edit)

        self.command_path_edit = QLineEdit()
        self.command_path_edit.setPlaceholderText("사용자 명령 모드에서만 필요합니다")
        self.command_path_edit.textChanged.connect(self._handle_command_changed)
        self.command_file_row = QWidget()
        command_row = QHBoxLayout(self.command_file_row)
        command_row.setContentsMargins(0, 0, 0, 0)
        command_row.addWidget(self.command_path_edit, 1)
        self.command_button = make_action_button(
            "선택",
            ActionKind.BROWSE,
            tooltip="사용자 명령 모드에서 실행할 명령 파일을 선택합니다.",
        )
        self.command_button.clicked.connect(self._pick_command_file)
        command_row.addWidget(self.command_button)
        command_layout.addRow(required_label("명령 파일"), self.command_file_row)
        self.command_file_label = command_layout.labelForField(self.command_file_row)
        self.command_text_label = command_layout.labelForField(self.command_text_edit)

        self.device_commands_panel = QWidget()
        self.device_commands_panel.setObjectName("inspectorDeviceCommandsPanel")
        device_commands_layout = QHBoxLayout(self.device_commands_panel)
        device_commands_layout.setContentsMargins(0, 0, 0, 0)
        self.device_commands_label = QLabel()
        self.device_commands_label.setWordWrap(True)
        self.device_commands_clear_button = make_action_button(
            "받은 CLI 지우기",
            ActionKind.DELETE,
            tooltip="설정 명령 만들기에서 받은 장비별 CLI를 지우고 직접 입력으로 돌아갑니다.",
        )
        self.device_commands_clear_button.clicked.connect(self.clear_device_commands)
        device_commands_layout.addWidget(self.device_commands_label, 1)
        device_commands_layout.addWidget(self.device_commands_clear_button)
        command_layout.addRow("받은 CLI", self.device_commands_panel)
        self.device_commands_row_label = command_layout.labelForField(self.device_commands_panel)

        self.command_check_label = make_inline_status(
            "info", "실행할 명령을 한 줄에 하나씩 입력하세요."
        )
        self.command_check_label.setObjectName("inspectorCustomCommandCheck")
        self.command_feedback = QWidget()
        self.command_feedback.setObjectName("inspectorCustomCommandFeedback")
        feedback_layout = QVBoxLayout(self.command_feedback)
        feedback_layout.setContentsMargins(0, 0, 0, 0)
        feedback_layout.setSpacing(6)
        feedback_layout.addWidget(self.command_check_label)
        self.command_variable_hint = QLabel(
            "장비 목록 Excel의 열 값은 {{ 열이름 }}으로 넣습니다. 예: show interface {{ interface }}\n"
            "값은 장비마다 바뀌며 결과와 세션 로그에 기록될 수 있습니다."
        )
        self.command_variable_hint.setObjectName("customCommandVariableHint")
        feedback_layout.addWidget(self.command_variable_hint)

        self.command_preview = QWidget()
        self.command_preview.setObjectName("inspectorCustomCommandPreview")
        preview_layout = QVBoxLayout(self.command_preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        self.command_preview_title = QLabel("실행 미리보기")
        self.command_preview_title.setObjectName("inspectorCustomCommandPreviewTitle")
        self.command_preview_view = QPlainTextEdit()
        self.command_preview_view.setReadOnly(True)
        self.command_preview_view.setAccessibleName("실행 미리보기")
        self.command_preview_view.setFixedHeight(84)
        preview_layout.addWidget(self.command_preview_title)
        preview_layout.addWidget(self.command_preview_view)
        feedback_layout.addWidget(self.command_preview)
        feedback_layout.addStretch(1)
        command_layout.addRow("", self.command_feedback)
        self.command_preview.hide()
        execution_layout.addRow(self.command_fields)

        self._command_check_timer = QTimer(self)
        self._command_check_timer.setSingleShot(True)
        self._command_check_timer.setInterval(250)
        self._command_check_timer.timeout.connect(self._refresh_command_check)

        self.execution_options_section = CollapsibleSection("실행 옵션")
        execution_options = QFormLayout()
        self.execution_options_section.content_layout.addLayout(execution_options)

        self.max_workers_spin = NoWheelSpinBox()
        self.max_workers_spin.setRange(1, 128)
        self.max_workers_spin.setValue(10)
        execution_options.addRow("동시 작업 수", self.max_workers_spin)
        self.timeout_spin = NoWheelSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(10)
        execution_options.addRow("Timeout(초)", self.timeout_spin)
        self.retry_spin = NoWheelSpinBox()
        self.retry_spin.setRange(0, 20)
        self.retry_spin.setValue(3)
        execution_options.addRow("재시도", self.retry_spin)
        self.output_name_edit = QLineEdit("inspection_results.xlsx")
        execution_options.addRow("결과 파일명", self.output_name_edit)
        for option in (self.max_workers_spin, self.timeout_spin, self.retry_spin, self.output_name_edit):
            self.execution_options_section.watch(option)
        validation_group = QGroupBox("3. 검증 및 실행")
        self.validation_group = validation_group
        validation_group.setObjectName("inspectorValidationGroup")
        validation_layout = QVBoxLayout(validation_group)
        self.inventory_format_section = CollapsibleSection("장비 목록 작성 방법")
        self.inventory_guide_steps = QWidget()
        self.inventory_guide_steps.setObjectName("inspectorInventoryGuideSteps")
        guide_steps_layout = QGridLayout(self.inventory_guide_steps)
        guide_steps_layout.setContentsMargins(0, 0, 0, 0)
        for column, (title, description) in enumerate((
            ("1  샘플 생성", "장비 종류를 고르고 샘플 생성을 누릅니다."),
            ("2  장비 정보 입력", "한 행에 한 장비를 적고 저장합니다."),
            ("3  파일 선택 · 검증", "저장한 Excel을 선택하고 먼저 검증합니다."),
        )):
            step = QWidget()
            step.setObjectName("inspectorInventoryGuideCard")
            step_layout = QVBoxLayout(step)
            step_layout.setContentsMargins(10, 8, 10, 8)
            title_label = QLabel(title)
            title_label.setObjectName("inventoryGuideStepTitle")
            description_label = QLabel(description)
            description_label.setWordWrap(True)
            step_layout.addWidget(title_label)
            step_layout.addWidget(description_label, 1, Qt.AlignmentFlag.AlignTop)
            guide_steps_layout.addWidget(step, 0, column)
            guide_steps_layout.setColumnStretch(column, 1)
        self.inventory_format_section.content_layout.addWidget(self.inventory_guide_steps)
        self.inventory_example_table = QTableWidget(6, 2)
        self.inventory_example_table.setObjectName("inspectorInventoryExampleTable")
        self.inventory_example_table.setHorizontalHeaderLabels(["필수 열", "입력 예시"])
        self.inventory_example_table.verticalHeader().hide()
        self.inventory_example_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.inventory_example_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.inventory_example_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.inventory_example_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.inventory_example_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inventory_example_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for row, values in enumerate((
            ("ip", "192.0.2.10"),
            ("vendor", "cisco"),
            ("os", "ios"),
            ("connection_type", "ssh"),
            ("port", "22"),
            ("password", "장비 접속 암호"),
        )):
            for column, value in enumerate(values):
                self.inventory_example_table.setItem(row, column, make_table_item(value))
        self.inventory_example_table.resizeRowsToContents()
        self.inventory_example_table.setFixedHeight(
            self.inventory_example_table.horizontalHeader().height()
            + sum(self.inventory_example_table.rowHeight(row) for row in range(6)) + 4
        )
        self.inventory_format_section.content_layout.addWidget(self.inventory_example_table)
        optional_columns = QLabel("선택 열: username · enable_password · model (모델 전용 프로파일)")
        optional_columns.setWordWrap(True)
        self.inventory_format_section.content_layout.addWidget(optional_columns)
        top_layout.addWidget(execution_group)
        top_layout.addWidget(inventory_group)
        self.supporting_tools_group = QFrame()
        self.supporting_tools_group.setObjectName("inspectorSupportingTools")
        self.supporting_tools_group.setAccessibleName("보조 도구")
        supporting_layout = QVBoxLayout(self.supporting_tools_group)
        supporting_layout.setContentsMargins(12, 10, 12, 12)
        supporting_layout.setSpacing(6)
        self.supporting_tools_title = QLabel("보조 도구")
        self.supporting_tools_title.setObjectName("inspectorSupportingTitle")
        supporting_layout.addWidget(self.supporting_tools_title)
        for section, icon_name in (
            (self.inventory_format_section, "book-open"),
            (self.execution_options_section, "settings-2"),
            (self.profile_section, "server"),
        ):
            header = QWidget()
            header.setObjectName("inspectorSupportingHeader")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(8)
            category_icon = QLabel()
            category_icon.setObjectName("inspectorSupportingIcon")
            category_icon.setFixedSize(20, 20)
            category_icon.setPixmap(icon(icon_name, "#64748b", 20).pixmap(20, 20))
            category_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            header_layout.addWidget(category_icon)
            section.layout().removeWidget(section.toggle_button)
            header_layout.addWidget(section.toggle_button, 1)
            section.layout().insertWidget(0, header)
            section.content_layout.setContentsMargins(28, 6, 0, 4)
            supporting_layout.addWidget(section)
        self.validation_status_label = make_inline_status(
            "info", "대상 장비 목록을 선택한 뒤 먼저 검증을 권장합니다."
        )
        validation_layout.addWidget(self.validation_status_label)
        self.create_missing_profile_button = make_action_button(
            "이 장비용 점검 프로파일 만들기",
            ActionKind.PRIMARY,
            tooltip="목록에 있는데 프로파일이 없는 장비 종류로 점검 프로파일 만들기를 엽니다.",
            object_name="inspectorCreateMissingProfileButton",
        )
        self.create_missing_profile_button.clicked.connect(self._create_missing_profile)
        self.create_missing_profile_button.hide()
        missing_profile_row = QHBoxLayout()
        missing_profile_row.addWidget(self.create_missing_profile_button)
        missing_profile_row.addStretch(1)
        validation_layout.addLayout(missing_profile_row)

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
            tooltip="선택한 모드로 장비 작업 자동화를 시작합니다.",
        )
        self.run_button.clicked.connect(self._run_inspector)
        self.cancel_button = make_action_button(
            "중지",
            ActionKind.STOP,
            tooltip="실행 중인 장비 작업 자동화의 중지를 요청합니다.",
            object_name="inspectorCancelButton",
            enabled=False,
        )
        self.cancel_button.clicked.connect(self._cancel_inspector)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.cancel_button)
        self.first_device_only_check = make_visible_checkbox("첫 장비 1대만 먼저 실행")
        self.first_device_only_check.setObjectName("inspectorFirstDeviceOnlyCheck")
        self.first_device_only_check.setToolTip(
            "목록의 첫 장비에서만 실행해 결과를 확인한 뒤, 체크를 풀고 전체를 실행하세요."
        )
        action_row.addWidget(self.first_device_only_check)
        action_row.addStretch(1)
        validation_layout.addLayout(action_row)
        top_layout.addWidget(validation_group)
        top_layout.addSpacing(12)
        top_layout.addWidget(self.supporting_tools_group)
        top_layout.addStretch(1)

        result_group = QGroupBox("진행 및 결과")
        self.result_group = result_group
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
        self.open_result_button.hide()
        self.open_artifacts_button = make_action_button("폴더 열기", ActionKind.OPEN)
        self.open_artifacts_button.clicked.connect(self._open_artifacts)
        self.open_artifacts_button.setEnabled(True)
        self.retry_failed_button = make_action_button(
            "실패한 장비만 다시 실행",
            ActionKind.START,
            tooltip="방금 실패한 장비에만 같은 작업을 다시 실행합니다.",
            object_name="inspectorRetryFailedButton",
        )
        self.retry_failed_button.clicked.connect(self._retry_failed_devices)
        self.retry_failed_button.hide()
        self.backup_diff_button = make_action_button(
            "변경 내용 보기",
            ActionKind.UTILITY,
            tooltip="표에서 고른 장비의 이번 백업을 이전 백업과 비교합니다.",
            object_name="inspectorBackupDiffButton",
        )
        self.backup_diff_button.clicked.connect(self._show_selected_backup_diff)
        self.backup_diff_button.hide()
        self.result_log_toggle_button = make_action_button(
            "로그 보기",
            ActionKind.UTILITY,
            tooltip="검증 및 실행 상세 로그를 표시하거나 숨깁니다.",
        )
        self.result_log_toggle_button.setCheckable(True)
        self.result_log_toggle_button.toggled.connect(self._set_result_log_visible)
        result_button_row.addWidget(self.open_result_button)
        result_button_row.addWidget(self.retry_failed_button)
        result_button_row.addWidget(self.backup_diff_button)
        result_button_row.addWidget(self.open_artifacts_button)
        result_button_row.addWidget(self.result_log_toggle_button)
        result_button_row.addStretch(1)
        result_layout.addLayout(result_button_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("inspectorProgressBar")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m대")
        self.progress_bar.setMaximumHeight(18)
        self.progress_bar.hide()
        result_layout.addWidget(self.progress_bar)

        self.summary_label = QLabel(
            "검증 결과와 실행 진행 상태가 여기에 표시됩니다."
        )
        self.summary_label.setObjectName("inspectorSummaryLabel")
        self.summary_label.setWordWrap(True)
        result_layout.addWidget(self.summary_label)

        self.result_table = QTableWidget(0, 4)
        self.result_table.setObjectName("inspectorResultTable")
        self.result_table.setHorizontalHeaderLabels(["IP", "장비", "결과", "내용"])
        self.result_table.setAccessibleName("장비별 실행 결과")
        self.result_table.verticalHeader().hide()
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        result_header = self.result_table.horizontalHeader()
        for column in range(3):
            result_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        result_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.result_table.setMinimumHeight(120)
        self.result_table.itemSelectionChanged.connect(self._update_backup_diff_button)
        self.result_table.cellDoubleClicked.connect(
            lambda _row, _column: self._show_selected_backup_diff()
        )
        self.result_table.hide()
        result_layout.addWidget(self.result_table, 2)

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
            QWidget#inspectorTopPanel {
                background: #f3f6fb;
            }
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

    def _toggle_inventory_password(self, enabled: bool) -> None:
        self.inventory_password_fields.setVisible(enabled)
        self._invalidate_validation("Excel 암호 사용 여부가 변경되었습니다. 다시 검증하세요.")
        self._update_run_action_state()

    def _inventory_password(self) -> str | None:
        if self.inventory_password_check.isChecked():
            return self.inventory_password_edit.text().strip() or None
        return None

    def _toggle_common_account(self, enabled: bool) -> None:
        self.common_account_fields.setVisible(enabled)
        self._invalidate_validation("공통 계정 사용 여부가 변경되었습니다. 다시 검증하세요.")
        self._update_run_action_state()

    def _handle_common_account_changed(self) -> None:
        if self.common_account_check.isChecked():
            self._invalidate_validation("공통 계정이 변경되었습니다. 다시 검증하세요.")
        self._update_run_action_state()

    def _default_credentials(self) -> dict[str, str] | None:
        """Account values that fill blank inventory cells, never saved to disk."""
        if not self.common_account_check.isChecked():
            return None
        values = {
            "username": self.common_username_edit.text().strip(),
            "password": self.common_password_edit.text(),
            "enable_password": self.common_enable_edit.text(),
        }
        values = {key: value for key, value in values.items() if value}
        return values or None

    def _update_command_file_state(self) -> None:
        custom_mode = self.mode_combo.currentData() == "custom_commands"
        enabled = custom_mode and not self._inspector_running
        inline = self._uses_inline_commands()
        device = self._uses_device_commands()
        self.command_path_edit.setEnabled(enabled)
        self.command_button.setEnabled(enabled)
        self.command_inline_radio.setEnabled(enabled)
        self.command_file_radio.setEnabled(enabled)
        self.command_device_radio.setEnabled(enabled)
        self.command_device_radio.setVisible(bool(self._device_commands))
        self.device_commands_clear_button.setEnabled(enabled)
        self.command_fields.setVisible(custom_mode)
        self.command_variable_hint.setVisible(custom_mode and not device)
        self.command_text_edit.setVisible(inline)
        self.command_text_label.setVisible(inline)
        file_mode = not inline and not device
        self.command_file_row.setVisible(file_mode)
        self.command_file_label.setVisible(file_mode)
        self.device_commands_panel.setVisible(device)
        self.device_commands_row_label.setVisible(device)
        if enabled:
            self.command_path_edit.setPlaceholderText("명령 파일을 선택하세요")
        else:
            self.command_path_edit.setPlaceholderText("명령 일괄 입력에서만 필요합니다")
        if custom_mode:
            self._command_check_timer.start()
        self._update_run_action_state()

    def _uses_inline_commands(self) -> bool:
        return self.command_inline_radio.isChecked()

    def _uses_device_commands(self) -> bool:
        return self.command_device_radio.isChecked() and bool(self._device_commands)

    def _inline_commands(self) -> list[str]:
        return [
            line.strip()
            for line in self.command_text_edit.toPlainText().splitlines()
            if line.strip()
        ]

    def _has_command_input(self) -> bool:
        if self._uses_device_commands():
            return True
        if self._uses_inline_commands():
            return bool(self._inline_commands())
        return bool(self.command_path_edit.text().strip())

    def _missing_command_message(self) -> str:
        if self._uses_inline_commands():
            return "명령 일괄 입력에서는 실행할 명령을 먼저 입력하세요."
        return "명령 일괄 입력에서는 명령 파일을 먼저 선택하세요."

    def current_mode_button(self) -> QPushButton:
        key = str(self.mode_combo.currentData() or "inspection")
        return self.mode_buttons.get(key, self.mode_buttons["inspection"])

    def _sync_mode_buttons(self) -> None:
        key = str(self.mode_combo.currentData() or "inspection")
        button = self.mode_buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        description = next(
            (text for mode, _label, text in MODE_CHOICES if mode == key), ""
        )
        self.mode_description_label.setText(description)

    def _handle_mode_changed(self) -> None:
        self._sync_mode_buttons()
        self._invalidate_validation(
            "실행 모드가 변경되었습니다. 현재 설정을 다시 검증하세요."
        )
        self._update_command_file_state()

    def _handle_command_source_changed(self, checked: bool = True) -> None:
        if not checked:
            return
        self._invalidate_validation(
            "명령 입력 방식이 변경되었습니다. 현재 설정을 다시 검증하세요."
        )
        self._update_command_file_state()

    def _handle_command_changed(self) -> None:
        message = (
            "실행할 명령이 변경되었습니다. 현재 설정을 다시 검증하세요."
            if self._uses_inline_commands()
            else "명령 파일이 변경되었습니다. 현재 설정을 다시 검증하세요."
        )
        self._invalidate_validation(message)
        self._command_check_timer.start()
        self._update_run_action_state()

    def _refresh_command_check(self) -> None:
        """Show command count, variables and syntax errors without connecting."""
        if self.mode_combo.currentData() != "custom_commands":
            return
        if self._uses_device_commands():
            set_inline_status(
                self.command_check_label,
                "success",
                f"장비 {len(self._device_commands)}대의 CLI를 받았습니다. "
                "장비 목록에서 IP가 같은 장비에만 각자의 CLI를 실행합니다.",
            )
            return
        if self._uses_inline_commands():
            commands = self._inline_commands()
            if not commands:
                set_inline_status(
                    self.command_check_label,
                    "info",
                    "실행할 명령을 한 줄에 하나씩 입력하세요.",
                )
                return
        else:
            command_path = self.command_path_edit.text().strip()
            if not command_path:
                set_inline_status(
                    self.command_check_label,
                    "info",
                    "명령 파일(.txt 또는 Excel)을 선택하세요. "
                    ".txt는 한 줄에, Excel은 첫 번째 열의 한 칸에 명령 하나를 적습니다.",
                )
                return
            if not Path(command_path).is_file():
                set_inline_status(
                    self.command_check_label,
                    "warning",
                    "선택한 명령 파일을 찾을 수 없습니다. 경로를 확인하세요.",
                )
                return
            try:
                commands = self.service.read_command_file(command_path)
            except Exception as exc:
                set_inline_status(
                    self.command_check_label,
                    "error",
                    f"명령 파일을 읽지 못했습니다: {exc}",
                )
                return
        try:
            summary = self.service.inspect_command_patterns(commands)
        except Exception as exc:
            set_inline_status(self.command_check_label, "error", str(exc))
            return
        variables = (
            ", ".join(summary.variable_names) if summary.variable_names else "없음"
        )
        set_inline_status(
            self.command_check_label,
            "success",
            f"명령 {summary.command_count}개 · 사용 변수 {variables}",
        )

    def _show_command_preview(
        self, summary: CustomCommandValidationSummary | None
    ) -> None:
        preview_device = getattr(summary, "preview_device", None)
        if not preview_device:
            self.command_preview.hide()
            self.command_preview_view.clear()
            return
        self.command_preview_title.setText(
            f"실행 미리보기 · 첫 장비 {preview_device}"
        )
        self.command_preview_view.setPlainText(
            "\n".join(getattr(summary, "preview_commands", ()) or ())
        )
        self.command_preview.show()

    def _handle_inventory_password_changed(self) -> None:
        self._invalidate_validation(
            "Excel 암호가 변경되었습니다. 대상 장비 목록을 다시 검증하세요."
        )
        self._update_run_action_state()

    def _invalidate_validation(self, message: str) -> None:
        self._inventory_validated = False
        self._custom_command_validation = None
        if hasattr(self, "command_preview"):
            self._show_command_preview(None)
        if hasattr(self, "validation_status_label"):
            set_inline_status(self.validation_status_label, "warning", message)

    def _handle_inventory_changed(self) -> None:
        self._inventory_validated = False
        self._custom_command_validation = None
        self._show_command_preview(None)
        had_previous_result = self._last_result is not None
        self._last_result = None
        self._last_request = None
        self._result_open_busy = False
        self._set_missing_profile(None)
        if hasattr(self, "result_table"):
            self._clear_result_table()
            self.retry_failed_button.hide()
            self.backup_diff_button.hide()
            self.progress_bar.hide()
        if hasattr(self, "open_result_button"):
            self.open_result_button.setEnabled(False)
            self.open_result_button.hide()
        if hasattr(self, "open_artifacts_button"):
            self.open_artifacts_button.setEnabled(True)
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
        has_command = self._has_command_input()
        enabled = (
            has_inventory
            and (not needs_command or has_command)
            and not self._inspector_running
        )
        self.run_button.setEnabled(enabled)
        self.validate_button.setEnabled(enabled)
        self.inventory_edit_button.setEnabled(not self._inspector_running)
        self.inventory_edit_button.setText(
            "표에서 편집" if has_inventory else "표에서 만들기"
        )
        self.run_button.setText("실행" if self._inventory_validated else "검증 후 실행")
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
                    "실행 중인 장비 작업 자동화의 중지를 요청합니다."
                )
            else:
                self.cancel_button.setToolTip(
                    "장비 작업 자동화를 실행하는 동안 사용할 수 있습니다."
                )
        if not has_inventory:
            self.run_button.setToolTip(
                "대상 장비 목록 Excel 파일을 선택하면 실행할 수 있습니다."
            )
        elif needs_command and not has_command:
            self.run_button.setToolTip(self._missing_command_message())
        else:
            self.run_button.setToolTip(
                "선택한 모드로 장비 작업 자동화를 시작합니다."
            )

    def _set_supported_profiles_visible(self, visible: bool) -> None:
        self.supported_label.setVisible(visible)
        self.supported_table.setVisible(visible)

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
        rows = self.build_sample_inventory_rows()
        df = pd.DataFrame(rows)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            df.to_excel(target, index=False)
        except Exception as exc:
            QMessageBox.warning(
                self, "샘플 생성 실패", f"샘플 장비 목록을 저장하지 못했습니다.\n{exc}"
            )
            return None
        self.inventory_path_edit.setText(str(target))
        profile = self._selected_sample_profile()
        device_label = " / ".join(
            value for value in (profile["vendor"], profile["os"], profile["model"]) if value
        )
        extra_columns = [
            column for column in rows[0] if column not in SAMPLE_BASE_COLUMNS
        ]
        extra_text = (
            f" 명령 변수 열({', '.join(extra_columns)})도 넣었습니다."
            if extra_columns
            else ""
        )
        set_inline_status(
            self.inventory_status_label,
            "success",
            f"{device_label} 샘플을 만들고 선택했습니다. "
            f"IP와 계정을 실제 값으로 바꾼 뒤 검증하세요.{extra_text} ({target})",
        )
        QMessageBox.information(
            self,
            "샘플 생성 완료",
            f"{device_label} 장비 2대가 들어간 샘플을 저장하고 선택했습니다.{extra_text}\n"
            f"Excel에서 IP와 계정을 실제 값으로 바꾼 뒤 검증하세요.\n{target}",
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
            connection = (
                profile.get("effective_connection_overrides")
                or profile.get("connection_overrides")
                or {}
            )
            device_type = connection.get("default") or connection.get("ssh") or "-"
            values = [
                profile["vendor"],
                profile["os"],
                profile.get("model") or "-",
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
        self._load_sample_profile_choices(profiles)

    def _load_sample_profile_choices(self, profiles: list[dict]) -> None:
        current = self.sample_profile_combo.currentData()
        self._sample_profiles = [
            {
                "vendor": str(profile.get("vendor", "")),
                "os": str(profile.get("os", "")),
                "model": str(profile.get("model", "") or ""),
            }
            for profile in profiles
        ]
        keys = [
            (profile["vendor"], profile["os"], profile["model"])
            for profile in self._sample_profiles
        ]
        self.sample_profile_combo.blockSignals(True)
        self.sample_profile_combo.clear()
        for index, (vendor, os_name, model) in enumerate(keys):
            label = f"{vendor} / {os_name}" + (f" / {model}" if model else "")
            self.sample_profile_combo.addItem(label, index)
        if isinstance(current, int) and 0 <= current < len(keys):
            selected = current
        else:
            preferred = ("cisco", "ios", "")
            selected = keys.index(preferred) if preferred in keys else 0
        if keys:
            self.sample_profile_combo.setCurrentIndex(selected)
        self.sample_profile_combo.blockSignals(False)

    def _sync_sample_profile_from_table(self) -> None:
        rows = {index.row() for index in self.supported_table.selectedIndexes()}
        if len(rows) == 1:
            row = rows.pop()
            if 0 <= row < self.sample_profile_combo.count():
                self.sample_profile_combo.setCurrentIndex(row)

    def _selected_sample_profile(self) -> dict[str, str]:
        index = self.sample_profile_combo.currentData()
        profiles = getattr(self, "_sample_profiles", [])
        if isinstance(index, int) and 0 <= index < len(profiles):
            return profiles[index]
        return {"vendor": "cisco", "os": "ios", "model": ""}

    def _sample_command_variables(self) -> tuple[str, ...]:
        if self.mode_combo.currentData() != "custom_commands" or self._uses_device_commands():
            return ()
        try:
            if self._uses_inline_commands():
                commands = self._inline_commands()
            else:
                command_path = self.command_path_edit.text().strip()
                if not command_path or not Path(command_path).is_file():
                    return ()
                commands = self.service.read_command_file(command_path)
            if not commands:
                return ()
            summary = self.service.inspect_command_patterns(commands)
            return tuple(summary.variable_names)
        except Exception:
            return ()

    def build_sample_inventory_rows(self) -> list[dict]:
        """Sample rows matched to the chosen device, task and command variables."""
        profile = self._selected_sample_profile()
        try:
            connection_types = self.service.handler_connection_types(
                profile["vendor"], profile["os"]
            )
        except Exception:
            connection_types = ()
        return build_sample_inventory_rows(
            vendor=profile["vendor"],
            os_name=profile["os"],
            model=profile["model"],
            mode=str(self.mode_combo.currentData() or "inspection"),
            connection_type=sample_connection_type(connection_types),
            variable_names=self._sample_command_variables(),
        )

    def _validate_inventory(self) -> None:
        path = self.inventory_path_edit.text().strip()
        if not path:
            set_inline_status(
                self.validation_status_label,
                "warning",
                "대상 장비 목록 Excel 파일을 먼저 선택하세요.",
            )
            mark_missing(self.inventory_path_edit)
            return
        mode = self.mode_combo.currentData()
        command_path = self.command_path_edit.text().strip()
        if mode == "custom_commands" and not self._has_command_input():
            set_inline_status(
                self.validation_status_label,
                "warning",
                self._missing_command_message(),
            )
            mark_missing(
                self.command_text_edit
                if self._uses_inline_commands()
                else self.command_path_edit
            )
            return
        if (
            self.common_account_check.isChecked()
            and not self.common_password_edit.text()
        ):
            set_inline_status(
                self.validation_status_label,
                "warning",
                "공통 계정을 쓰려면 비밀번호를 입력하세요. 쓰지 않으면 '공통 계정 사용'을 끄세요.",
            )
            mark_missing(self.common_password_edit)
            return
        self._set_missing_profile(None)
        default_credentials = self._default_credentials()
        try:
            if default_credentials:
                devices = self.service.load_inventory(
                    path, self._inventory_password(), default_credentials
                )
            else:
                devices = self.service.load_inventory(
                    path, self._inventory_password()
                )
            profile_warnings = self.service.inventory_profile_warnings(devices)
            command_validation = None
            if mode == "custom_commands" and self._uses_device_commands():
                command_validation = self.service.validate_device_commands(
                    self._device_commands, devices
                )
            elif mode == "custom_commands" and self._uses_inline_commands():
                command_validation = self.service.validate_custom_commands(
                    self._inline_commands(),
                    devices,
                )
            elif mode == "custom_commands":
                command_validation = self.service.validate_custom_command_file(
                    command_path,
                    devices,
                )
        except Exception as exc:
            self._inventory_validated = False
            self._custom_command_validation = None
            self._update_run_action_state()
            self._log_inspector_exception("대상 장비 목록 검증 실패", exc)
            message = self._inspector_error_message(exc)
            missing_profiles = self._unsupported_profiles_in(path)
            if missing_profiles:
                self._set_missing_profile(missing_profiles[0])
                names = ", ".join(f"{vendor} / {os_name}" for vendor, os_name in missing_profiles)
                message += (
                    f"\n\n점검 프로파일이 없는 장비 종류: {names}. "
                    "아래 버튼으로 이 장비용 점검 프로파일을 만들 수 있습니다."
                )
            set_inline_status(self.validation_status_label, "error", message)
            QMessageBox.warning(self, "검증 실패", message)
            return
        self._inventory_validated = True
        self._custom_command_validation = command_validation
        self._show_command_preview(command_validation)
        validation_summary = f"검증 완료: 장비 {len(devices)}대"
        if isinstance(command_validation, DeviceCommandValidationSummary):
            validation_summary += (
                f" · 장비별 CLI를 받을 장비 {len(command_validation.matched_ips)}대"
            )
            if command_validation.skipped_ips:
                validation_summary += (
                    f" · CLI가 없어 건너뛸 장비 {len(command_validation.skipped_ips)}대"
                )
            if command_validation.missing_ips:
                validation_summary += (
                    " · 목록에 없는 IP "
                    + ", ".join(command_validation.missing_ips[:_FAILED_PREVIEW_LIMIT])
                    + (" …" if len(command_validation.missing_ips) > _FAILED_PREVIEW_LIMIT else "")
                )
        elif command_validation is not None:
            variables = (
                ", ".join(command_validation.variable_names)
                if command_validation.variable_names
                else "없음"
            )
            validation_summary += (
                f" · 명령 {command_validation.command_count}개 · 사용 변수 {variables}"
            )
        if profile_warnings:
            validation_summary += f" · 모델 프로파일 경고 {len(profile_warnings)}건"
        self.summary_label.setText(validation_summary)
        device_gaps = isinstance(command_validation, DeviceCommandValidationSummary) and bool(
            command_validation.missing_ips or command_validation.skipped_ips
        )
        set_inline_status(
            self.validation_status_label,
            "warning" if profile_warnings or device_gaps else "success",
            validation_summary
            + (
                "\n" + "\n".join(profile_warnings)
                if profile_warnings
                else ""
            ),
        )
        self._append_result_log(
            f"[validate] {Path(path).name}: {validation_summary}"
        )
        for warning in profile_warnings:
            self._append_result_log(f"[warning] {warning}")
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
            mark_missing(self.inventory_path_edit)
            return
        mode = self.mode_combo.currentData()
        if mode == "custom_commands" and not self._has_command_input():
            set_inline_status(
                self.validation_status_label,
                "warning",
                self._missing_command_message(),
            )
            mark_missing(
                self.command_text_edit
                if self._uses_inline_commands()
                else self.command_path_edit
            )
            return
        if not self._inventory_validated:
            self._validate_inventory()
            if not self._inventory_validated:
                return
        custom_validation_detail = ""
        device_commands = mode == "custom_commands" and self._uses_device_commands()
        if device_commands and isinstance(
            self._custom_command_validation, DeviceCommandValidationSummary
        ):
            custom_validation_detail = (
                f" 설정 명령 만들기에서 받은 장비별 CLI를 장비 "
                f"{len(self._custom_command_validation.matched_ips)}대에 실행합니다. "
                "설정을 바꾸는 명령이므로 변경 승인과 작업 시간을 확인하세요."
                + self._confirmation_command_preview(self._custom_command_validation)
            )
        elif mode == "custom_commands" and self._custom_command_validation is not None:
            variables = (
                ", ".join(self._custom_command_validation.variable_names)
                if self._custom_command_validation.variable_names
                else "없음"
            )
            custom_validation_detail = (
                f" 명령 {self._custom_command_validation.command_count}개와 "
                f"변수({variables})가 검증되었습니다."
                + self._confirmation_command_preview(self._custom_command_validation)
            )
        first_device_only = self.first_device_only_check.isChecked()
        scope = (
            "목록의 첫 장비 1대에만 SSH/Telnet 접속을 시도합니다. "
            if first_device_only
            else f"목록에 있는 장비에 SSH/Telnet 접속을 시도합니다. 최대 {self.max_workers_spin.value()}대가 동시에 처리되며 "
        )
        if self._default_credentials():
            scope += "Excel에서 비어 있는 계정·비밀번호 칸에는 공통 계정을 씁니다. "
        if not confirm_risky_action(
            self,
            "첫 장비 시험 실행" if first_device_only else "대량 장비 작업 실행",
            impact=(
                scope
                + "일부 장비에서 로그인 실패, 세션 잠금, 네트워크 부하가 발생할 수 있습니다."
                + custom_validation_detail
            ),
            reversibility="점검·백업은 장비 설정을 변경하지 않습니다. 명령 일괄 입력은 명령 내용에 따라 되돌리기 어려울 수 있습니다.",
            output_location="결과 Excel, 백업 파일, 세션 로그, 원본 명령 출력(raw output)은 설정에 지정한 결과 폴더에 기록됩니다.",
            question="현재 대상 장비 목록과 실행 모드를 확인한 뒤 진행할까요?",
            confirm_text=f"{MODE_LABELS.get(str(mode), '작업')} 실행",
        ):
            return
        inline_commands = (
            mode == "custom_commands"
            and self._uses_inline_commands()
        )
        file_commands = (
            mode == "custom_commands" and not inline_commands and not device_commands
        )
        request = InspectorRunRequest(
            inventory_path=path,
            mode=mode,
            inventory_password=self._inventory_password(),
            command_path=(self.command_path_edit.text().strip() or None)
            if file_commands else None,
            commands=self._inline_commands() if inline_commands else None,
            output_name=self.output_name_edit.text().strip()
            or "inspection_results.xlsx",
            max_workers=self.max_workers_spin.value(),
            timeout=self.timeout_spin.value(),
            max_retries=self.retry_spin.value(),
            device_limit=1 if first_device_only else None,
            default_credentials=self._default_credentials(),
            commands_by_ip=dict(self._device_commands) if device_commands else None,
        )
        self._start_run(request)

    def _start_run(self, request: InspectorRunRequest) -> None:
        self.log_view.clear()
        self.summary_label.setText(
            f"{MODE_LABELS.get(request.mode, '장비 작업')} 실행 중입니다. 장비 목록을 불러오는 중..."
        )
        self._last_result = None
        self._last_request = request
        self._result_open_busy = False
        self._inspector_running = True
        self._cancel_event = Event()
        self._run_progress = {"total": 0, "done": 0, "success": 0, "failed": 0}
        self._clear_result_table()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self._set_result_log_visible(False)
        self._set_run_controls_locked(True)
        self._update_run_action_state()
        self.open_result_button.setEnabled(False)
        self.open_result_button.hide()
        self.retry_failed_button.hide()
        self.backup_diff_button.hide()
        self.open_artifacts_button.setEnabled(True)
        self.runner.start(
            self.service.run,
            request,
            cancel_event=self._cancel_event,
            on_progress=self._handle_progress,
            on_result=self._handle_result,
            on_finished=self._finish_inspector_run,
            on_error=self._handle_error,
        )

    @staticmethod
    def _confirmation_command_preview(
        summary: CustomCommandValidationSummary,
    ) -> str:
        preview_device = getattr(summary, "preview_device", None)
        preview_commands = tuple(getattr(summary, "preview_commands", ()) or ())
        if not preview_device or not preview_commands:
            return ""
        visible = preview_commands[:_CONFIRM_PREVIEW_LIMIT]
        lines = [f"\n\n실행할 명령(첫 장비 {preview_device} 기준):"]
        lines.extend(f"  {command}" for command in visible)
        hidden_count = len(preview_commands) - len(visible)
        if hidden_count > 0:
            lines.append(f"  … 외 {hidden_count}개")
        return "\n".join(lines)

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
        self._append_result_log(
            "[cancel] 사용자가 장비 점검 작업 중지를 요청했습니다."
        )
        self._update_run_action_state()

    def _append_result_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText("\n".join(
            f"[{timestamp}] {line}" for line in message.splitlines()
        ))

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
        self.inventory_edit_button.setEnabled(not locked)
        self.sample_button.setEnabled(not locked)
        self.inventory_password_check.setEnabled(not locked)
        self.common_account_check.setEnabled(not locked)
        self.create_missing_profile_button.setEnabled(not locked)
        self.validate_button.setEnabled(not locked)
        for edit in (
            self.inventory_path_edit,
            self.inventory_password_edit,
            self.command_path_edit,
            self.command_text_edit,
            self.output_name_edit,
            self.common_username_edit,
            self.common_password_edit,
            self.common_enable_edit,
        ):
            edit.setReadOnly(locked)
        for widget in (
            self.mode_combo,
            self.max_workers_spin,
            self.timeout_spin,
            self.retry_spin,
            self.first_device_only_check,
            *self.mode_buttons.values(),
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
            self._append_result_log(f"[{event_type}] {message}")
        progress = self._run_progress
        if event_type == "progress" and isinstance(event.get("total"), int):
            progress["total"] = int(event["total"])
            self.progress_bar.setRange(0, max(progress["total"], 1))
            self.progress_bar.setValue(progress["done"])
            self._show_running_summary()
        elif event_type == "device_complete":
            progress["done"] += 1
            if event.get("success"):
                progress["success"] += 1
            else:
                progress["failed"] += 1
                self._append_result_log(f"[failed] {event.get('ip', '')}")
            if progress["total"]:
                self.progress_bar.setValue(min(progress["done"], progress["total"]))
            self._show_running_summary()

    def _show_running_summary(self) -> None:
        progress = self._run_progress
        mode = MODE_LABELS.get(
            self._last_request.mode if self._last_request else "", "장비 작업"
        )
        total = progress["total"]
        if not total:
            return
        text = f"{mode} 실행 중 · {progress['done']}/{total}대 완료"
        if progress["done"]:
            text += f" (성공 {progress['success']} · 실패 {progress['failed']})"
        self.summary_label.setText(text)

    def _handle_result(self, result: object) -> None:
        if self._shutting_down:
            return
        if not isinstance(result, InspectorRunResult):
            return
        self._last_result = result
        self._result_open_busy = False
        rows = [row for row in result.results if isinstance(row, dict)]
        failed = [row for row in rows if self._is_failed_result(row)]
        mode = MODE_LABELS.get(result.mode, "장비 작업")
        total = result.devices_total
        if not failed:
            summary = f"{mode} 완료: {total}대 모두 성공했습니다."
        else:
            summary = (
                f"{mode} 완료: {total}대 중 {total - len(failed)}대 성공, "
                f"{len(failed)}대 실패했습니다."
            )
        self._backup_comparisons = self._compare_backups(result, rows)
        changed = [c for c in self._backup_comparisons.values() if c.status == "changed"]
        if self._backup_comparisons:
            same = sum(1 for c in self._backup_comparisons.values() if c.status == "same")
            new = sum(1 for c in self._backup_comparisons.values() if c.status == "new")
            summary += (
                f" 백업 비교: 변경 {len(changed)}대 · 이전과 같음 {same}대 · 첫 백업 {new}대."
            )
        self.summary_label.setText(summary)
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(total)
        if failed:
            preview = ", ".join(
                str(row.get("ip", "")) for row in failed[:_FAILED_PREVIEW_LIMIT]
            )
            if len(failed) > _FAILED_PREVIEW_LIMIT:
                preview += f" 외 {len(failed) - _FAILED_PREVIEW_LIMIT}대"
            set_inline_status(
                self.validation_status_label,
                "warning",
                f"{len(failed)}대가 실패했습니다({preview}). 아래 표의 '내용'에서 원인과 조치를 확인한 뒤 "
                "'실패한 장비만 다시 실행'을 누르세요.",
            )
        else:
            set_inline_status(
                self.validation_status_label,
                "success",
                "실행이 완료되었습니다. 아래 표와 결과 Excel에서 결과를 확인하세요.",
            )
        self._fill_result_table(rows)
        self.retry_failed_button.setVisible(bool(failed))
        self.retry_failed_button.setText(f"실패한 {len(failed)}대만 다시 실행")
        self.backup_diff_button.setVisible(bool(changed))
        self._update_backup_diff_button()
        self.open_result_button.setEnabled(bool(result.result_excel))
        self.open_result_button.setVisible(bool(result.result_excel))
        self.open_artifacts_button.setEnabled(True)

    @staticmethod
    def _is_failed_result(row: dict) -> bool:
        return str(row.get("status", "")).strip().lower() == "error"

    def _compare_backups(
        self, result: InspectorRunResult, rows: list[dict]
    ) -> dict[str, BackupComparison]:
        if result.mode not in {"backup", "inspection_backup"} or not result.backup_dir:
            return {}
        backup_dir = Path(result.backup_dir)
        comparisons: dict[str, BackupComparison] = {}
        for row in rows:
            if self._is_failed_result(row):
                continue
            try:
                path = backup_file_for_result(
                    row, backup_dir, getattr(self.service, "work_dir", None)
                )
                comparisons[str(row.get("ip", ""))] = compare_backup_file(path)
            except OSError as exc:
                self._append_result_log(
                    f"[warning] 백업 비교 실패 {row.get('ip', '')}: {exc}"
                )
        return comparisons

    def _clear_result_table(self) -> None:
        self._result_rows = []
        self._backup_comparisons = {}
        self.result_table.setRowCount(0)
        self.result_table.hide()

    def _fill_result_table(self, rows: list[dict]) -> None:
        ordered = sorted(
            enumerate(rows),
            key=lambda pair: (not self._is_failed_result(pair[1]), pair[0]),
        )
        self._result_rows = [row for _index, row in ordered]
        self.result_table.setRowCount(len(self._result_rows))
        for table_row, row in enumerate(self._result_rows):
            failed = self._is_failed_result(row)
            ip = str(row.get("ip", ""))
            device = " / ".join(
                str(value)
                for value in (row.get("vendor", ""), row.get("os", ""))
                if value
            )
            comparison = self._backup_comparisons.get(ip)
            if failed:
                detail = " ".join(
                    str(row.get("error_message", "") or "알 수 없는 오류").split()
                )
            elif comparison is not None:
                detail = comparison.label
            elif row.get("custom_commands_executed") is not None:
                detail = f"명령 {row.get('custom_commands_executed')}개 실행"
            else:
                detail = "완료"
            values = (ip, device, "실패" if failed else "성공", detail)
            for column, value in enumerate(values):
                item = make_table_item(value)
                if column == 2:
                    item.setForeground(
                        QColor("#b42318") if failed else QColor("#15803d")
                    )
                self.result_table.setItem(table_row, column, item)
        self.result_table.setVisible(bool(self._result_rows))
        if self._result_rows:
            self.inspector_splitter.setSizes([360, 340])

    def _selected_result_row(self) -> dict | None:
        row = self.result_table.currentRow()
        if 0 <= row < len(self._result_rows):
            return self._result_rows[row]
        return None

    def _update_backup_diff_button(self) -> None:
        row = self._selected_result_row()
        comparison = (
            self._backup_comparisons.get(str(row.get("ip", ""))) if row else None
        )
        changed = comparison is not None and comparison.status == "changed"
        self.backup_diff_button.setEnabled(changed)
        self.backup_diff_button.setToolTip(
            "고른 장비의 이번 백업을 이전 백업과 비교합니다."
            if changed
            else "표에서 '변경됨'인 장비를 고르세요."
        )

    def _show_selected_backup_diff(self) -> None:
        row = self._selected_result_row()
        if row is None:
            return
        comparison = self._backup_comparisons.get(str(row.get("ip", "")))
        if comparison is None or comparison.status != "changed":
            return
        label = f"{row.get('ip', '')} ({row.get('vendor', '')} / {row.get('os', '')})"
        BackupDiffDialog(label, comparison, self).exec()

    def failed_device_ips(self) -> list[str]:
        if self._last_result is None:
            return []
        return [
            str(row.get("ip", "")).strip()
            for row in self._last_result.results
            if isinstance(row, dict)
            and self._is_failed_result(row)
            and str(row.get("ip", "")).strip()
        ]

    def _retry_failed_devices(self) -> None:
        if self._inspector_running or self._shutting_down or self._last_request is None:
            return
        failed = self.failed_device_ips()
        if not failed:
            return
        preview = ", ".join(failed[:_FAILED_PREVIEW_LIMIT])
        if len(failed) > _FAILED_PREVIEW_LIMIT:
            preview += f" 외 {len(failed) - _FAILED_PREVIEW_LIMIT}대"
        if not confirm_risky_action(
            self,
            "실패한 장비 다시 실행",
            impact=(
                f"방금 실패한 장비 {len(failed)}대({preview})에만 같은 작업을 다시 실행합니다. "
                "장비 목록 Excel은 지금 파일을 다시 읽습니다."
            ),
            reversibility="점검·백업은 장비 설정을 변경하지 않습니다. 명령 일괄 입력은 같은 명령을 다시 보냅니다.",
            output_location="새 결과 Excel과 세션 로그가 결과 폴더에 따로 만들어집니다.",
            question="실패한 장비만 다시 실행할까요?",
            confirm_text="다시 실행",
        ):
            return
        self._start_run(
            replace(self._last_request, only_ips=tuple(failed), device_limit=None)
        )

    def _handle_error(self, text: str) -> None:
        if self._shutting_down:
            return
        cancel_requested = bool(
            self._cancel_event is not None and self._cancel_event.is_set()
        )
        cancelled = cancel_requested and any(
            marker in str(text) for marker in ("취소", "중지")
        )
        self.progress_bar.hide()
        if cancelled:
            self.summary_label.setText("장비 점검 작업이 중지되었습니다.")
            set_inline_status(
                self.validation_status_label,
                "warning",
                "사용자 요청으로 장비 점검 작업을 중지했습니다.",
            )
            self._append_result_log(
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
        try:
            if self._last_result:
                for candidate in (
                    self._last_result.backup_dir,
                    self._last_result.session_log_dir,
                    Path(self._last_result.result_excel).parent
                    if self._last_result.result_excel else None,
                ):
                    if candidate and Path(candidate).is_dir():
                        os.startfile(str(candidate))
                        return
            self.service.work_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.service.work_dir))
        except OSError as exc:
            self._append_result_log(f"[error] 결과 폴더 열기 실패: {exc}")
            QMessageBox.warning(self, "결과 폴더 열기 실패", str(exc))

    def _inventory_rows_for_tools(self) -> list[dict] | None:
        """Rows of the chosen device list, read without profile validation."""
        path = self.inventory_path_edit.text().strip()
        if not path or not Path(path).is_file():
            return None
        try:
            return self.service.load_inventory_rows(
                path, self._inventory_password(), self._default_credentials()
            )
        except Exception as exc:
            self._log_inspector_exception("장비 목록 읽기 실패", exc)
            return None

    def _unsupported_profiles_in(self, path: str) -> list[tuple[str, str]]:
        try:
            rows = self.service.load_inventory_rows(path, self._inventory_password())
            return self.service.unsupported_inventory_profiles(rows)
        except Exception:
            return []

    def _set_missing_profile(self, pair: tuple[str, str] | None) -> None:
        self._missing_profile = pair
        if not hasattr(self, "create_missing_profile_button"):
            return
        if pair is None:
            self.create_missing_profile_button.hide()
            return
        self.create_missing_profile_button.setText(
            f"{pair[0]} / {pair[1]} 점검 프로파일 만들기"
        )
        self.create_missing_profile_button.show()

    def _create_missing_profile(self) -> None:
        if self._missing_profile is not None:
            self._open_profile_editor(initial_device=self._missing_profile)

    def _open_inventory_editor(self) -> None:
        if self._inspector_running or self._shutting_down:
            return
        path = self.inventory_path_edit.text().strip()
        rows: list[dict] = []
        source_path: Path | None = None
        if path and Path(path).is_file():
            if self.inventory_password_check.isChecked():
                QMessageBox.information(
                    self,
                    "장비 목록 편집",
                    "암호가 있는 Excel은 이 화면에서 편집할 수 없습니다. Excel에서 직접 고치거나, "
                    "'암호가 있는 Excel'을 끄고 새 목록을 만드세요.",
                )
                return
            try:
                rows = self.service.load_inventory_rows(path)
            except Exception as exc:
                QMessageBox.warning(
                    self, "장비 목록 편집", f"장비 목록을 읽지 못했습니다.\n{exc}"
                )
                return
            source_path = Path(path)
        profile = self._selected_sample_profile()
        device_types = [
            (item["vendor"], item["os"]) for item in getattr(self, "_sample_profiles", [])
        ]
        try:
            connection = sample_connection_type(
                self.service.handler_connection_types(profile["vendor"], profile["os"])
            )
        except Exception:
            connection = "ssh"
        dialog = InventoryEditorDialog(
            device_types,
            self,
            rows=rows,
            extra_columns=self._sample_command_variables(),
            default_device=(profile["vendor"], profile["os"]),
            default_connection=connection,
            save_dir=self.exports_dir,
            source_path=source_path,
        )
        if dialog.exec() and dialog.saved_path is not None:
            saved = str(dialog.saved_path)
            if self.inventory_path_edit.text().strip() == saved:
                self._handle_inventory_changed()
            else:
                self.inventory_path_edit.setText(saved)
            set_inline_status(
                self.inventory_status_label,
                "success",
                f"장비 목록을 저장하고 선택했습니다. 다음 단계: 먼저 검증 ({saved})",
            )
            self.validate_button.setFocus()

    def receive_device_commands(
        self, commands_by_ip: dict[str, list[str]], source: str = ""
    ) -> bool:
        """Take per-device CLI from the config builder and switch to command mode."""
        if self._inspector_running or self._shutting_down:
            QMessageBox.information(
                self,
                "장비 점검으로 보내기",
                "장비 점검이 실행 중입니다. 끝난 뒤 다시 보내세요.",
            )
            return False
        cleaned = {
            str(ip).strip(): self.service.clean_device_commands(list(lines))
            for ip, lines in commands_by_ip.items()
            if str(ip).strip()
        }
        cleaned = {ip: lines for ip, lines in cleaned.items() if lines}
        if not cleaned:
            return False
        self._device_commands = cleaned
        self._device_commands_source = source
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("custom_commands"))
        self.command_device_radio.show()
        self.command_device_radio.setChecked(True)
        source_text = f" ({source})" if source else ""
        self.device_commands_label.setText(
            f"장비 {len(cleaned)}대의 CLI{source_text}. 장비 목록에서 같은 IP의 장비에만 각자의 CLI를 실행합니다."
        )
        self.first_device_only_check.setChecked(True)
        self._invalidate_validation(
            "설정 명령 만들기에서 장비별 CLI를 받았습니다. 같은 IP가 있는 장비 목록을 고르고 먼저 검증하세요."
        )
        self._update_command_file_state()
        self._refresh_command_check()
        return True

    def clear_device_commands(self) -> None:
        self._device_commands = {}
        self._device_commands_source = ""
        self.command_inline_radio.setChecked(True)
        self.command_device_radio.hide()
        self._invalidate_validation("받은 장비별 CLI를 지웠습니다. 현재 설정을 다시 검증하세요.")
        self._update_command_file_state()

    def _open_profile_editor(self, initial_device: tuple[str, str] | None = None) -> None:
        if self._inspector_running or self._shutting_down:
            return
        try:
            self._profile_dialog = InspectorProfileDialog(
                self.service,
                self,
                exports_dir=self.exports_dir,
                inventory_rows=self._inventory_rows_for_tools(),
                initial_device=initial_device,
            )
        except Exception as exc:
            self._log_inspector_exception("장비 프로파일 관리 열기 실패", exc)
            QMessageBox.warning(
                self, "장비 프로파일 관리 열기 실패", self._inspector_error_message(exc)
            )
            return
        self._profile_dialog.exec()
        self._load_supported_profiles()
        self._invalidate_validation("장비 프로파일을 확인했습니다. 현재 목록을 다시 검증하세요.")
        self._update_run_action_state()

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
            self._append_result_log(f"[error] {message}: {exc}")
