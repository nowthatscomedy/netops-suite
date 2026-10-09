from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from threading import Event

import pandas as pd
from PySide6.QtCore import QTimer, Qt
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
    set_inline_status,
    set_table_minimums,
)
from app.ui.dialogs.inspector_profile_dialog import InspectorProfileDialog
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
            "프로파일 만들기·관리",
            ActionKind.PRIMARY,
            tooltip="사용자 프로파일을 만들거나 제조사·모델·OS별 점검 명령과 결과 열을 편집합니다.",
            object_name="inspectorProfileEditorButton",
        )
        self.profile_editor_button.clicked.connect(self._open_profile_editor)
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
        inventory_layout.addWidget(QLabel("장비 목록 Excel"))
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
            tooltip=(
                "선택한 장비 종류와 실행할 작업에 맞는 열과 예시 값으로 "
                "장비 목록 Excel을 만듭니다."
            ),
        )
        self.sample_button.clicked.connect(self._create_sample_inventory)
        inventory_row.addWidget(self.inventory_button)
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
        inventory_layout.addWidget(self.inventory_password_check)
        self.inventory_password_fields = QWidget()
        password_layout = QFormLayout(self.inventory_password_fields)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.addRow("Excel 암호", self.inventory_password_edit)
        inventory_layout.addWidget(self.inventory_password_fields)
        self.inventory_password_fields.hide()
        self.inventory_password_check.toggled.connect(self._toggle_inventory_password)
        self.inventory_status_label = make_inline_status(
            "info", "대상 장비 목록 파일을 선택하거나 샘플을 생성하세요."
        )
        inventory_layout.addWidget(self.inventory_status_label)

        execution_group = QGroupBox("1. 실행할 작업")
        execution_group.setObjectName("inspectorExecutionGroup")
        execution_layout = QFormLayout(execution_group)
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("점검", "inspection")
        self.mode_combo.addItem("백업", "backup")
        self.mode_combo.addItem("점검+백업", "inspection_backup")
        self.mode_combo.addItem("사용자 명령", "custom_commands")
        self.mode_combo.currentIndexChanged.connect(self._handle_mode_changed)
        execution_layout.addRow("실행 모드", self.mode_combo)

        self.command_fields = QWidget()
        self.command_fields.setObjectName("inspectorCustomCommandFields")
        command_layout = QFormLayout(self.command_fields)
        command_layout.setContentsMargins(0, 0, 0, 0)

        source_row = QHBoxLayout()
        self.command_inline_radio = QRadioButton("직접 입력")
        self.command_inline_radio.setToolTip("명령을 이 화면에 한 줄에 하나씩 입력합니다.")
        self.command_file_radio = QRadioButton("명령 파일")
        self.command_file_radio.setToolTip("미리 만든 .txt 또는 Excel 명령 파일을 사용합니다.")
        self.command_source_group = QButtonGroup(self)
        self.command_source_group.addButton(self.command_inline_radio)
        self.command_source_group.addButton(self.command_file_radio)
        self.command_inline_radio.setChecked(True)
        self.command_inline_radio.toggled.connect(self._handle_command_source_changed)
        source_row.addWidget(self.command_inline_radio)
        source_row.addWidget(self.command_file_radio)
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
        command_layout.addRow("실행할 명령", self.command_text_edit)

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
        command_layout.addRow("사용자 명령 파일", self.command_file_row)
        self.command_file_label = command_layout.labelForField(self.command_file_row)
        self.command_text_label = command_layout.labelForField(self.command_text_edit)

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
            "검증 결과와 실행 진행 상태가 여기에 표시됩니다."
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

    def _update_command_file_state(self) -> None:
        custom_mode = self.mode_combo.currentData() == "custom_commands"
        enabled = custom_mode and not self._inspector_running
        inline = self._uses_inline_commands()
        self.command_path_edit.setEnabled(enabled)
        self.command_button.setEnabled(enabled)
        self.command_inline_radio.setEnabled(enabled)
        self.command_file_radio.setEnabled(enabled)
        self.command_fields.setVisible(custom_mode)
        self.command_variable_hint.setVisible(custom_mode)
        self.command_text_edit.setVisible(inline)
        self.command_text_label.setVisible(inline)
        self.command_file_row.setVisible(not inline)
        self.command_file_label.setVisible(not inline)
        if enabled:
            self.command_path_edit.setPlaceholderText("사용자 명령 파일을 선택하세요")
        else:
            self.command_path_edit.setPlaceholderText("사용자 명령 모드에서만 필요합니다")
        if custom_mode:
            self._command_check_timer.start()
        self._update_run_action_state()

    def _uses_inline_commands(self) -> bool:
        return self.command_inline_radio.isChecked()

    def _inline_commands(self) -> list[str]:
        return [
            line.strip()
            for line in self.command_text_edit.toPlainText().splitlines()
            if line.strip()
        ]

    def _has_command_input(self) -> bool:
        if self._uses_inline_commands():
            return bool(self._inline_commands())
        return bool(self.command_path_edit.text().strip())

    def _missing_command_message(self) -> str:
        if self._uses_inline_commands():
            return "사용자 명령 모드에서는 실행할 명령을 먼저 입력하세요."
        return "사용자 명령 모드에서는 명령 파일을 먼저 선택하세요."

    def _handle_mode_changed(self) -> None:
        self._invalidate_validation(
            "실행 모드가 변경되었습니다. 현재 설정을 다시 검증하세요."
        )
        self._update_command_file_state()

    def _handle_command_source_changed(self) -> None:
        self._invalidate_validation(
            "명령 입력 방식이 변경되었습니다. 현재 설정을 다시 검증하세요."
        )
        self._update_command_file_state()

    def _handle_command_changed(self) -> None:
        message = (
            "실행할 명령이 변경되었습니다. 현재 설정을 다시 검증하세요."
            if self._uses_inline_commands()
            else "사용자 명령 파일이 변경되었습니다. 현재 설정을 다시 검증하세요."
        )
        self._invalidate_validation(message)
        self._command_check_timer.start()
        self._update_run_action_state()

    def _refresh_command_check(self) -> None:
        """Show command count, variables and syntax errors without connecting."""
        if self.mode_combo.currentData() != "custom_commands":
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
        self._result_open_busy = False
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
        if self.mode_combo.currentData() != "custom_commands":
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
            return
        mode = self.mode_combo.currentData()
        command_path = self.command_path_edit.text().strip()
        if mode == "custom_commands" and not self._has_command_input():
            set_inline_status(
                self.validation_status_label,
                "warning",
                self._missing_command_message(),
            )
            return
        try:
            devices = self.service.load_inventory(
                path, self._inventory_password()
            )
            profile_warnings = self.service.inventory_profile_warnings(devices)
            command_validation = None
            if mode == "custom_commands" and self._uses_inline_commands():
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
            set_inline_status(
                self.validation_status_label,
                "error",
                self._inspector_error_message(exc),
            )
            QMessageBox.warning(self, "검증 실패", self._inspector_error_message(exc))
            return
        self._inventory_validated = True
        self._custom_command_validation = command_validation
        self._show_command_preview(command_validation)
        validation_summary = f"검증 완료: 장비 {len(devices)}대"
        if command_validation is not None:
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
        set_inline_status(
            self.validation_status_label,
            "warning" if profile_warnings else "success",
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
            return
        mode = self.mode_combo.currentData()
        if mode == "custom_commands" and not self._has_command_input():
            set_inline_status(
                self.validation_status_label,
                "warning",
                self._missing_command_message(),
            )
            return
        if not self._inventory_validated:
            self._validate_inventory()
            if not self._inventory_validated:
                return
        custom_validation_detail = ""
        if mode == "custom_commands" and self._custom_command_validation is not None:
            variables = (
                ", ".join(self._custom_command_validation.variable_names)
                if self._custom_command_validation.variable_names
                else "없음"
            )
            custom_validation_detail = (
                f" 사용자 명령 {self._custom_command_validation.command_count}개와 "
                f"변수({variables})가 검증되었습니다."
                + self._confirmation_command_preview(self._custom_command_validation)
            )
        if not confirm_risky_action(
            self,
            "대량 장비 점검 실행",
            impact=(
                f"목록에 있는 장비에 SSH/Telnet 접속을 시도합니다. 최대 {self.max_workers_spin.value()}대가 동시에 처리되며 "
                "일부 장비에서 로그인 실패, 세션 잠금, 네트워크 부하가 발생할 수 있습니다."
                + custom_validation_detail
            ),
            reversibility="기본 점검/백업 모드는 장비 설정을 변경하지 않습니다. 사용자 명령 모드는 명령 파일 내용에 따라 되돌리기 어려울 수 있습니다.",
            output_location="결과 Excel, 백업 파일, 세션 로그, 원본 명령 출력(raw output)은 설정에 지정한 결과 폴더에 기록됩니다.",
            question="현재 대상 장비 목록과 실행 모드를 확인한 뒤 진행할까요?",
            confirm_text="점검/백업 실행",
        ):
            return
        inline_commands = (
            mode == "custom_commands" and self._uses_inline_commands()
        )
        request = InspectorRunRequest(
            inventory_path=path,
            mode=mode,
            inventory_password=self._inventory_password(),
            command_path=(self.command_path_edit.text().strip() or None)
            if mode == "custom_commands" and not inline_commands else None,
            commands=self._inline_commands() if inline_commands else None,
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
        self._set_result_log_visible(False)
        self._set_run_controls_locked(True)
        self._update_run_action_state()
        self.open_result_button.setEnabled(False)
        self.open_result_button.hide()
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
        self.sample_button.setEnabled(not locked)
        self.inventory_password_check.setEnabled(not locked)
        self.validate_button.setEnabled(not locked)
        for edit in (
            self.inventory_path_edit,
            self.inventory_password_edit,
            self.command_path_edit,
            self.command_text_edit,
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
            self._append_result_log(f"[{event_type}] {message}")

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
        self.open_result_button.setVisible(bool(result.result_excel))
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

    def _open_profile_editor(self) -> None:
        if self._inspector_running or self._shutting_down:
            return
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
