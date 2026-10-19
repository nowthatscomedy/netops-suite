from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from threading import Event

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QTextEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.disclosure import CollapsibleSection
from app.ui.common.required import (
    MISSING_STYLE,
    mark_missing,
    required_label,
    required_legend_text,
    set_required_label,
)
from app.ui.common import (
    JobRunner,
    confirm_risky_action,
    make_dialog_intro,
    make_inline_status,
    make_visible_checkbox,
    polish_dialog,
    set_inline_status,
)
from app.utils.file_utils import timestamped_export_path
from netops_suite.modules.inspector import InspectorService
from netops_suite.modules.inspector.extraction_suggest import (
    ExtractionSuggestion,
    SelectionError,
    extract_value,
    suggest_extractions,
)
from netops_suite.modules.inspector.service import ProfileTrialResult
from netops_suite.ui.actions import ActionKind, make_action_button
from netops_suite.ui.numeric_inputs import NoWheelSpinBox
from netops_suite.ui.selection_inputs import NoWheelComboBox

ERROR_BG = QColor("#fff1ed")
OK_BG = QColor("#eef8eb")
_DEVICE_TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,63}\Z")
_TRIAL_OUTPUT_PREVIEW_LINES = 12
_TRIAL_SESSION_MAX_LINES = 5000


@lru_cache(maxsize=1)
def _supported_netmiko_device_types() -> frozenset[str] | None:
    try:
        from netmiko.ssh_dispatcher import CLASS_MAPPER
    except (ImportError, RuntimeError):
        return None
    return frozenset(str(name) for name in CLASS_MAPPER)


def _has_unsafe_single_line_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class PythonParserDialog(QDialog):
    def __init__(self, service: InspectorService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.saved_function_name = ""
        self.setWindowTitle("Python 추출 함수 만들기")
        self.resize(920, 720)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        polish_dialog(self, layout)
        hint = make_dialog_intro(
            "명령어 출력 전체가 output 문자열로 들어옵니다. Excel에 넣을 값 하나를 return 하거나, "
            "여러 컬럼을 만들 때는 {'컬럼명': '값'} 형태의 dict를 return 하세요."
        )
        layout.addWidget(hint)

        warning = QLabel(
            "주의: Python 추출 함수는 이 프로그램 권한으로 실행됩니다. "
            "직접 작성했거나 신뢰할 수 있는 코드만 테스트하고 저장하세요."
        )
        warning.setObjectName("pythonParserTrustWarning")
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background:transparent; color:#475467; border:0; "
            "border-left:3px solid #fdba74; padding:4px 0 4px 9px;"
        )
        layout.addWidget(warning)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.function_name_edit = QLineEdit("parsing_custom_value")
        self.function_name_edit.setPlaceholderText("예: parsing_cpu_usage")
        form.addRow("함수 이름", self.function_name_edit)
        layout.addLayout(form)

        self.code_edit = QPlainTextEdit()
        self.code_edit.setPlainText(
            "def parsing_custom_value(output: str):\n"
            "    for line in output.splitlines():\n"
            '        if "CPU Usage" in line:\n'
            "            parts = line.split()\n"
            '            return " ".join(parts[-2:])\n'
            '    return ""\n'
        )
        layout.addWidget(QLabel("Python 코드"))
        layout.addWidget(self.code_edit, 3)

        self.sample_output_edit = QPlainTextEdit()
        self.sample_output_edit.setPlainText(
            "CPU Usage        12 %\nMemory Usage     40 %"
        )
        layout.addWidget(QLabel("테스트용 출력 예시"))
        layout.addWidget(self.sample_output_edit, 1)

        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMaximumHeight(90)
        layout.addWidget(QLabel("테스트 결과"))
        layout.addWidget(self.result_view)

        actions = QHBoxLayout()
        test_button = make_action_button(
            "테스트",
            ActionKind.START,
            tooltip="작성한 Python 추출 함수를 테스트합니다.",
        )
        test_button.clicked.connect(self._test_code)
        save_button = make_action_button("저장", ActionKind.SAVE)
        save_button.clicked.connect(self._save_code)
        close_button = make_action_button("닫기", ActionKind.CANCEL)
        close_button.clicked.connect(self.reject)
        actions.addStretch(1)
        actions.addWidget(test_button)
        actions.addWidget(save_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def _test_code(self) -> None:
        try:
            result = self.service.test_custom_parser_code(
                self.function_name_edit.text(),
                self.code_edit.toPlainText(),
                self.sample_output_edit.toPlainText(),
            )
        except Exception as exc:
            self.result_view.setPlainText(f"실패: {exc}")
            return
        self.result_view.setPlainText(repr(result))

    def _save_code(self) -> None:
        try:
            path = self.service.save_custom_parser(
                self.function_name_edit.text(),
                self.code_edit.toPlainText(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return
        self.saved_function_name = path.stem
        QMessageBox.information(
            self, "저장 완료", f"Python 추출 함수를 저장했습니다.\n{path}"
        )
        self.accept()


class InspectorProfileDialog(QDialog):
    def __init__(
        self,
        service: InspectorService,
        parent=None,
        *,
        exports_dir: str | Path | None = None,
        inventory_rows: list[dict[str, Any]] | None = None,
        initial_device: tuple[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        # Devices from the inspector's chosen list, offered on the trial tab.
        self.inventory_rows = [
            row for row in (inventory_rows or []) if str(row.get("ip", "")).strip()
        ]
        self.exports_dir = (
            Path(exports_dir)
            if exports_dir is not None
            else self.service.user_data_dir / "exports"
        )
        self.profiles = service.supported_profile_definitions()
        self.state = self._empty_state()
        self.latest_yaml_text = ""
        self._selected_command_row = -1
        self._selected_column_row = -1
        self._loading_command = False
        self._loading_column = False
        self._loading_state = True
        self._dirty = False
        self._last_profile_index = 0
        self._parser_dialog: PythonParserDialog | None = None
        self._trial_runner = JobRunner(
            QThreadPool.globalInstance(), self, default_error_title="장비 시험 실패"
        )
        self._trial_cancel: Event | None = None
        self._trial_running = False
        self._trial_session_dir: str | None = None
        self._trial_ip_flagged = False
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(180)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.refresh_preview)

        self.setWindowTitle("점검 프로파일 만들기")
        self.resize(1120, 780)
        self._build_ui()
        self._load_profile_choices()
        self._load_state()
        if initial_device is not None:
            self._apply_initial_device(*initial_device)
        self.refresh_preview()
        self._loading_state = False
        self._dirty = False

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "profile_scope": "base",
            "vendor": "",
            "model": "",
            "os": "",
            "os_version": "",
            "connection_type": "ssh",
            "ssh_device_type": "",
            "telnet_device_type": "",
            "commands": [
                {
                    "command": "show version",
                    "sample": "Cisco IOS XE Software, Version 17.09.04\ncisco C9300-24T processor\nProcessor board ID FOC1234ABCD",
                },
                {"command": "show inventory", "sample": ""},
            ],
            "backup_enabled": True,
            "backup_command": "show running-config",
            "columns": [
                {
                    "name": "OS버전",
                    "command": "show version",
                    "method": "split_fields",
                    "line_number": 1,
                    "start_field": 6,
                    "end_field": 6,
                    "keyword": "",
                    "regex": "",
                    "python_parser": "",
                },
                {
                    "name": "시리얼번호",
                    "command": "show version",
                    "method": "keyword_after",
                    "line_number": 3,
                    "start_field": 4,
                    "end_field": 4,
                    "keyword": "Processor board ID",
                    "regex": "",
                    "python_parser": "",
                },
            ],
        }

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        polish_dialog(self, layout)
        self.setStyleSheet(self.styleSheet() + MISSING_STYLE)
        self._show_missing_required = False
        intro = make_dialog_intro(
            "이 프로파일은 장비에 명령어를 실행하고, 출력값을 Excel 컬럼으로 정리합니다. "
            "명령을 입력하고 장비로 시험해 출력을 가져온 뒤, 출력에서 Excel에 넣을 값을 마우스로 고르세요."
        )
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        self._build_device_tab()
        self._build_command_tab()
        self._build_backup_tab()
        self._build_trial_tab()
        self._build_column_tab()
        self._build_review_tab()
        self._build_advanced_tab()
        self.tabs.currentChanged.connect(self._update_next_step_button)

        actions = QHBoxLayout()
        self.trial_shortcut_button = make_action_button(
            "장비로 시험",
            ActionKind.SECONDARY,
            tooltip="저장하기 전에 실제 장비 한 대에서 명령을 실행해 확인합니다.",
        )
        self.trial_shortcut_button.setObjectName("profileTrialShortcutButton")
        self.trial_shortcut_button.clicked.connect(
            lambda: self.tabs.setCurrentWidget(self.trial_tab)
        )
        actions.addWidget(self.trial_shortcut_button)
        self.required_legend = QLabel()
        self.required_legend.setObjectName("profileRequiredLegend")
        self.required_legend.setWordWrap(True)
        self._show_required_legend()
        actions.addWidget(self.required_legend, 1)
        refresh_button = make_action_button(
            "갱신", ActionKind.REFRESH, tooltip="YAML 미리보기를 갱신합니다."
        )
        refresh_button.clicked.connect(self.refresh_preview)
        self.save_button = make_action_button(
            "저장", ActionKind.SAVE, tooltip="프로파일을 저장합니다."
        )
        self.save_button.clicked.connect(self._save_profile)
        self.close_button = make_action_button("닫기", ActionKind.CANCEL)
        self.close_button.clicked.connect(self._request_close)
        self.next_step_button = make_action_button(
            "다음", ActionKind.PRIMARY, tooltip="다음 단계로 이동합니다."
        )
        self.next_step_button.setObjectName("profileNextStepButton")
        self.next_step_button.clicked.connect(self._go_to_next_step)
        actions.addStretch(1)
        actions.addWidget(self.next_step_button)
        actions.addWidget(refresh_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self._update_next_step_button()

    def _step_tabs(self) -> list[QWidget]:
        return [self.tabs.widget(index) for index in range(self.tabs.count())][
            : self.tabs.indexOf(self.review_tab) + 1
        ]

    def _update_next_step_button(self, _index: int | None = None) -> None:
        steps = self._step_tabs()
        current = self.tabs.currentWidget()
        if current in steps[:-1]:
            target = steps[steps.index(current) + 1]
            title = self.tabs.tabText(self.tabs.indexOf(target)).split(". ", 1)[-1]
            self.next_step_button.setText(f"다음: {title}")
            self.next_step_button.show()
        else:
            self.next_step_button.hide()
        self.trial_shortcut_button.setVisible(current is not self.trial_tab)
        if current is self.trial_tab and not self._trial_running:
            self._sort_trial_devices()

    def _go_to_next_step(self) -> None:
        steps = self._step_tabs()
        current = self.tabs.currentWidget()
        if current not in steps[:-1]:
            return
        # The device trial is optional, so only real profile fields block a step.
        if current is not self.trial_tab:
            self._collect_state()
            missing = self._missing_required_fields(current)
            if missing:
                self._show_missing_required = True
                self._refresh_required_marks()
                self._show_required_legend([name for _widget, name in missing])
                missing[0][0].setFocus()
                return
        self._show_required_legend()
        self.tabs.setCurrentWidget(steps[steps.index(current) + 1])

    def _show_required_legend(self, missing: list[str] | None = None) -> None:
        self.required_legend.setText(required_legend_text(missing))

    def _missing_required_fields(self, tab: QWidget | None = None) -> list[tuple[QWidget, str]]:
        """Required fields left empty, optionally only those on one step."""
        checks: list[tuple[QWidget, QWidget, str, bool]] = [
            (self.device_tab, self.vendor_edit, "벤더", not self.state["vendor"]),
            (self.device_tab, self.os_edit, "OS", not self.state["os"]),
            (
                self.device_tab,
                self.model_edit,
                "모델",
                self.state.get("profile_scope") == "model" and not self.state["model"],
            ),
            (
                self.command_tab,
                self.command_edit,
                "명령어",
                not self.command_edit.text().strip() or not self._commands(),
            ),
            (
                self.backup_tab,
                self.backup_command_edit,
                "백업 명령",
                self.state["backup_enabled"] and not self.state["backup_command"],
            ),
            (
                self.column_tab,
                self.column_name_edit,
                "Excel 컬럼명",
                self._selected_column_row >= 0 and not self.column_name_edit.text().strip(),
            ),
        ]
        return [
            (widget, name)
            for owner, widget, name, empty in checks
            if empty and (tab is None or owner is tab)
        ]

    def _refresh_required_marks(self) -> None:
        missing = (
            {widget for widget, _name in self._missing_required_fields()}
            if self._show_missing_required
            else set()
        )
        if (
            self._show_missing_required
            and self._trial_ip_flagged
            and not self.trial_ip_edit.text().strip()
        ):
            missing.add(self.trial_ip_edit)
        for widget in (
            self.vendor_edit,
            self.os_edit,
            self.model_edit,
            self.command_edit,
            self.backup_command_edit,
            self.column_name_edit,
            self.trial_ip_edit,
        ):
            mark_missing(widget, widget in missing)
        if self._show_missing_required and not missing:
            self._show_required_legend()

    def _build_device_tab(self) -> None:
        tab = QWidget()
        self.device_tab = tab
        layout = QVBoxLayout(tab)
        hint = QLabel(
            "어떤 장비의 점검 결과를 Excel로 정리할지 입력합니다. 벤더와 OS는 장비 목록 Excel의 "
            "vendor·os 칸에 그대로 쓰는 이름입니다. 지원 목록에 없는 장비라면 알아보기 쉬운 이름을 "
            "자유롭게 정하세요. 비슷한 장비의 프로파일이 있으면 '기존 프로파일 복사'로 시작하면 빠릅니다."
        )
        hint.setObjectName("profileDeviceHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.vendor_edit = QLineEdit()
        self.vendor_edit.setPlaceholderText("예: Cisco, Aruba, Juniper, Alcatel-Lucent")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("예: C9300-24T, Aruba 2930F, EX3400")
        self.os_edit = QLineEdit()
        self.os_edit.setPlaceholderText("예: IOS-XE, AOS6, Junos")
        self.os_version_edit = QLineEdit()
        self.os_version_edit.setPlaceholderText("예: 17.09, 6.5.4, 21.4R3")
        self.connection_combo = NoWheelComboBox()
        self.connection_combo.addItem("SSH", "ssh")
        self.connection_combo.addItem("Telnet", "telnet")
        self.copy_profile_combo = NoWheelComboBox()
        self.copy_profile_combo.currentIndexChanged.connect(self._copy_selected_profile)
        self.profile_scope_combo = NoWheelComboBox()
        self.profile_scope_combo.setObjectName("inspectorProfileScopeCombo")
        self.profile_scope_combo.addItem("벤더/OS 공통", "base")
        self.profile_scope_combo.addItem("특정 장비 모델 전용", "model")
        self.profile_scope_combo.setToolTip(
            "모델 전용은 장비 목록의 model 값이 정확히 일치할 때만 적용됩니다."
        )
        form.addRow("적용 범위", self.profile_scope_combo)
        form.addRow(required_label("벤더"), self.vendor_edit)
        self.model_label = required_label("모델", required=False)
        form.addRow(self.model_label, self.model_edit)
        form.addRow(required_label("OS"), self.os_edit)
        form.addRow("OS 버전", self.os_version_edit)
        form.addRow("접속 방식", self.connection_combo)
        form.addRow("기존 프로파일 복사", self.copy_profile_combo)
        layout.addLayout(form)
        layout.addStretch(1)
        self.tabs.addTab(tab, "1. 장비 정보")

        for widget in (
            self.vendor_edit,
            self.model_edit,
            self.os_edit,
            self.os_version_edit,
        ):
            widget.textChanged.connect(self._schedule_preview)
        self.connection_combo.currentIndexChanged.connect(self._schedule_preview)
        self.connection_combo.currentIndexChanged.connect(self._sync_trial_port)
        self.profile_scope_combo.currentIndexChanged.connect(
            self._on_profile_scope_changed
        )

    def _apply_initial_device(self, vendor: str, os_name: str) -> None:
        """Start a new profile for a device type the inventory needs."""
        self.vendor_edit.setText(vendor)
        self.os_edit.setText(os_name)
        self._sort_trial_devices()

    def _on_profile_scope_changed(self, _index: int | None = None) -> None:
        model_specific = self.profile_scope_combo.currentData() == "model"
        set_required_label(self.model_label, "모델", model_specific)
        self.model_edit.setToolTip(
            "장비 목록 Excel의 model 값과 대소문자·공백 정규화 후 정확히 일치해야 합니다."
            if model_specific
            else "공통 프로파일에서는 기존 profile_metadata의 참고 모델로 저장됩니다."
        )
        self._schedule_preview()

    def _build_command_tab(self) -> None:
        tab = QWidget()
        self.command_tab = tab
        tab.setObjectName("inspectionCommandsTab")
        layout = QHBoxLayout(tab)
        left = QVBoxLayout()
        left.addWidget(QLabel("점검 명령 목록"))
        self.command_list = QListWidget()
        self.command_list.setAccessibleName("점검 명령 목록")
        self.command_list.currentRowChanged.connect(self._on_command_selected)
        left.addWidget(self.command_list, 1)
        buttons = QHBoxLayout()
        add_button = make_action_button(
            "추가", ActionKind.ADD, tooltip="점검 명령을 추가합니다."
        )
        add_button.clicked.connect(self._add_command)
        remove_button = make_action_button(
            "삭제", ActionKind.DELETE, tooltip="선택한 점검 명령을 삭제합니다."
        )
        remove_button.clicked.connect(self._remove_command)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        left.addLayout(buttons)

        right = QVBoxLayout()
        right.addWidget(QLabel("선택한 점검 명령"))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("예: show version")
        self.sample_output_edit = QPlainTextEdit()
        self.sample_output_edit.setPlaceholderText(
            "비워 두어도 됩니다. '4. 장비로 시험'을 실행하면 장비 출력으로 채워집니다.\n"
            "장비에 접속할 수 없으면 출력을 여기에 붙여넣으세요."
        )
        form.addRow(required_label("명령어"), self.command_edit)
        form.addRow("출력 예시", self.sample_output_edit)
        right.addLayout(form, 1)

        layout.addLayout(left, 2)
        layout.addLayout(right, 4)
        self.tabs.addTab(tab, "2. 점검 명령")

        self.command_edit.textChanged.connect(self._on_command_changed)
        self.sample_output_edit.textChanged.connect(self._on_command_changed)

    def _build_backup_tab(self) -> None:
        tab = QWidget()
        self.backup_tab = tab
        tab.setObjectName("backupCommandTab")
        layout = QVBoxLayout(tab)

        intro = make_dialog_intro(
            "점검 결과와 별도로 장비의 전체 구성을 백업할 때 사용할 명령을 설정합니다. "
            "백업이 필요하지 않은 프로파일은 이 기능을 끌 수 있습니다."
        )
        layout.addWidget(intro)

        self.backup_enabled_check = QCheckBox("장비 구성 백업 사용")
        self.backup_enabled_check.setObjectName("backupEnabledCheck")
        self.backup_enabled_check.setToolTip(
            "켜면 점검 작업에서 아래 명령의 전체 출력을 구성 백업 파일로 저장합니다."
        )
        layout.addWidget(self.backup_enabled_check)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.backup_command_edit = QLineEdit()
        self.backup_command_edit.setObjectName("backupCommandEdit")
        self.backup_command_edit.setPlaceholderText(
            "예: show running-config, show configuration, display current-configuration"
        )
        self.backup_command_edit.setToolTip(
            "장비 구성을 백업할 때 한 번 실행할 명령을 입력합니다."
        )
        self.backup_command_label = required_label("백업 명령")
        form.addRow(self.backup_command_label, self.backup_command_edit)
        layout.addLayout(form)

        backup_hint = QLabel(
            "백업 명령의 출력은 Excel 컬럼 추출에 사용하지 않고, 구성 백업 파일 전체로 저장됩니다."
        )
        backup_hint.setObjectName("backupCommandHint")
        backup_hint.setWordWrap(True)
        layout.addWidget(backup_hint)
        layout.addStretch(1)

        self.tabs.addTab(tab, "3. 백업 명령")

        self.backup_enabled_check.toggled.connect(self._on_backup_enabled_changed)
        self.backup_command_edit.textChanged.connect(self._schedule_preview)
        self._on_backup_enabled_changed(False)

    def _on_backup_enabled_changed(self, enabled: bool) -> None:
        self.backup_command_edit.setEnabled(enabled)
        self._schedule_preview()

    def _build_column_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("profileColumnTab")
        self.column_tab = tab
        layout = QHBoxLayout(tab)
        left = QVBoxLayout()
        left.addWidget(QLabel("Excel에 표시할 컬럼"))
        self.column_list = QListWidget()
        self.column_list.setAccessibleName("Excel 컬럼 목록")
        self.column_list.currentRowChanged.connect(self._on_column_selected)
        left.addWidget(self.column_list, 1)
        buttons = QHBoxLayout()
        add_button = make_action_button(
            "추가", ActionKind.ADD, tooltip="빈 Excel 출력 컬럼을 추가합니다."
        )
        add_button.clicked.connect(self._add_column)
        remove_button = make_action_button(
            "삭제", ActionKind.DELETE, tooltip="선택한 출력 컬럼을 삭제합니다."
        )
        remove_button.clicked.connect(self._remove_column)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        left.addLayout(buttons)

        right = QVBoxLayout()
        source_row = QFormLayout()
        source_row.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.column_command_combo = NoWheelComboBox()
        self.column_command_combo.setAccessibleName("값을 가져올 명령")
        source_row.addRow("명령 출력", self.column_command_combo)
        right.addLayout(source_row)
        pick_hint = QLabel(
            "아래 출력에서 Excel에 넣을 값을 마우스로 드래그한 뒤 "
            "'선택한 값으로 컬럼 만들기'를 누르세요. 값을 다시 찾는 규칙은 자동으로 만들어집니다."
        )
        pick_hint.setObjectName("profileColumnPickHint")
        pick_hint.setWordWrap(True)
        right.addWidget(pick_hint)
        self.column_output_view = QPlainTextEdit()
        self.column_output_view.setObjectName("profileColumnOutputView")
        self.column_output_view.setReadOnly(True)
        self.column_output_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.column_output_view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.column_output_view.setAccessibleName("명령 출력에서 값 고르기")
        self.column_output_view.setPlaceholderText(
            "이 명령의 출력이 아직 없습니다. '장비로 시험'에서 장비 출력을 가져오거나 "
            "'점검 명령' 탭의 출력 예시에 붙여넣으세요."
        )
        right.addWidget(self.column_output_view, 3)
        pick_row = QHBoxLayout()
        self.pick_value_button = make_action_button(
            "선택한 값으로 컬럼 만들기",
            ActionKind.PRIMARY,
            tooltip="드래그해서 고른 값을 가져오는 Excel 컬럼을 만듭니다.",
        )
        self.pick_value_button.setObjectName("profileColumnPickButton")
        self.pick_value_button.clicked.connect(self._create_column_from_selection)
        self.suggestion_label = QLabel("찾는 방법")
        self.suggestion_combo = NoWheelComboBox()
        self.suggestion_combo.setObjectName("profileColumnSuggestionCombo")
        self.suggestion_combo.setAccessibleName("값을 찾는 방법")
        self.suggestion_combo.currentIndexChanged.connect(self._apply_selected_suggestion)
        pick_row.addWidget(self.pick_value_button)
        pick_row.addWidget(self.suggestion_label)
        pick_row.addWidget(self.suggestion_combo, 1)
        right.addLayout(pick_row)
        self._suggestions: list[ExtractionSuggestion] = []
        self._set_suggestions([])
        self.pick_status_label = make_inline_status("info", "")
        self.pick_status_label.setObjectName("profileColumnPickStatus")
        right.addWidget(self.pick_status_label)

        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.column_name_edit = QLineEdit()
        self.column_name_edit.setPlaceholderText("예: OS버전, 시리얼번호, CPU 사용률")
        self.preview_label = QLabel("추출 결과: -")
        self.preview_label.setObjectName("profileColumnPreview")
        self.preview_label.setWordWrap(True)
        form.addRow(required_label("Excel 컬럼명"), self.column_name_edit)
        form.addRow("", self.preview_label)
        right.addLayout(form)

        self.rule_section = CollapsibleSection("추출 규칙 직접 고치기")
        self.rule_section.setObjectName("profileColumnRuleSection")
        rule_form = QFormLayout()
        rule_form.setVerticalSpacing(8)
        rule_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.extract_method_combo = NoWheelComboBox()
        self.extract_method_combo.addItem(
            "몇 번째 줄/몇 번째 값 가져오기", "split_fields"
        )
        self.extract_method_combo.addItem("특정 단어 뒤의 값 가져오기", "keyword_after")
        self.extract_method_combo.addItem("줄 전체 가져오기", "line_text")
        self.extract_method_combo.addItem("정규식 직접 입력", "regex")
        self.extract_method_combo.addItem("Python 고급 추출", "python")
        self.line_number_spin = NoWheelSpinBox()
        self.line_number_spin.setRange(1, 10000)
        self.start_field_spin = NoWheelSpinBox()
        self.start_field_spin.setRange(1, 1000)
        self.end_field_spin = NoWheelSpinBox()
        self.end_field_spin.setRange(1, 1000)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("예: Processor board ID")
        self.regex_edit = QLineEdit()
        self.regex_edit.setPlaceholderText(r"예: Version\s+([0-9.]+)")
        self.python_parser_combo = NoWheelComboBox()
        self.python_parser_combo.setEditable(True)
        rule_form.addRow("추출 방식", self.extract_method_combo)
        rule_form.addRow("줄 번호", self.line_number_spin)
        rule_form.addRow("시작 값 번호", self.start_field_spin)
        rule_form.addRow("끝 값 번호", self.end_field_spin)
        rule_form.addRow("찾을 단어", self.keyword_edit)
        rule_form.addRow("정규식", self.regex_edit)
        rule_form.addRow("Python 추출 함수", self.python_parser_combo)
        self._extraction_method_fields = {
            "line_number": (rule_form.labelForField(self.line_number_spin), self.line_number_spin),
            "start_field": (rule_form.labelForField(self.start_field_spin), self.start_field_spin),
            "end_field": (rule_form.labelForField(self.end_field_spin), self.end_field_spin),
            "keyword": (rule_form.labelForField(self.keyword_edit), self.keyword_edit),
            "regex": (rule_form.labelForField(self.regex_edit), self.regex_edit),
            "python": (
                rule_form.labelForField(self.python_parser_combo),
                self.python_parser_combo,
            ),
        }
        self.rule_section.content_layout.addLayout(rule_form)
        self.rule_section.content_layout.addWidget(QLabel("줄·값 번호로 본 출력"))
        self.sample_line_view = QPlainTextEdit()
        self.sample_line_view.setReadOnly(True)
        self.sample_line_view.setAccessibleName("선택한 명령 출력 예시")
        self.sample_line_view.setMinimumHeight(140)
        self.rule_section.content_layout.addWidget(self.sample_line_view)
        right.addWidget(self.rule_section)

        layout.addLayout(left, 2)
        layout.addLayout(right, 5)
        self.tabs.addTab(tab, "5. Excel 컬럼")

        for widget in (self.column_name_edit, self.keyword_edit, self.regex_edit):
            widget.textChanged.connect(self._on_column_changed)
        self.column_command_combo.currentIndexChanged.connect(self._on_column_changed)
        self.extract_method_combo.currentIndexChanged.connect(
            self._on_extraction_method_changed
        )
        self.line_number_spin.valueChanged.connect(self._on_column_changed)
        self.start_field_spin.valueChanged.connect(self._on_column_changed)
        self.end_field_spin.valueChanged.connect(self._on_column_changed)
        self.python_parser_combo.currentTextChanged.connect(self._on_column_changed)
        self._update_extraction_method_fields()

    def _set_suggestions(self, suggestions: list[ExtractionSuggestion]) -> None:
        self._suggestions = list(suggestions)
        self.suggestion_combo.blockSignals(True)
        self.suggestion_combo.clear()
        for suggestion in self._suggestions:
            label = suggestion.description
            if suggestion.position_based:
                label += " (위치 기준)"
            self.suggestion_combo.addItem(label)
        self.suggestion_combo.blockSignals(False)
        visible = len(self._suggestions) > 1
        self.suggestion_label.setVisible(visible)
        self.suggestion_combo.setVisible(visible)

    def _create_column_from_selection(self) -> None:
        cursor = self.column_output_view.textCursor()
        command = self.column_command_combo.currentText().strip()
        sample = self.column_output_view.toPlainText()
        if not command or not sample.strip():
            set_inline_status(
                self.pick_status_label,
                "warning",
                "먼저 명령 출력을 준비하세요. '장비로 시험'에서 가져오거나 출력 예시에 붙여넣으면 됩니다.",
            )
            return
        try:
            suggestions = suggest_extractions(
                sample, cursor.selectionStart(), cursor.selectionEnd()
            )
        except SelectionError as exc:
            set_inline_status(self.pick_status_label, "warning", str(exc))
            return
        self._persist_column()
        best = suggestions[0]
        taken = {
            str(column.get("name", "")).casefold()
            for index, column in enumerate(self.state["columns"])
        }
        name = best.column_name
        number = 2
        while name.casefold() in taken:
            name = f"{best.column_name} {number}"
            number += 1
        self.state["columns"].append(best.as_column(command, name))
        self._refresh_column_list(len(self.state["columns"]) - 1)
        self._set_suggestions(suggestions)
        self._show_pick_result(best)
        self.column_name_edit.setFocus()
        self.column_name_edit.selectAll()
        self._schedule_preview()

    def _apply_selected_suggestion(self, index: int) -> None:
        row = self._selected_column_row
        if not (0 <= index < len(self._suggestions)) or row < 0:
            return
        suggestion = self._suggestions[index]
        current = self.state["columns"][row]
        self.state["columns"][row] = suggestion.as_column(
            current.get("command", ""), current.get("name", "")
        )
        self._load_column(row)
        self._show_pick_result(suggestion)
        self._schedule_preview()

    def _show_pick_result(self, suggestion: ExtractionSuggestion) -> None:
        message = f"{suggestion.description}으로 '{suggestion.value}'를 가져옵니다. 컬럼 이름을 확인하세요."
        if suggestion.position_based:
            message += " 줄 위치 기준이라 출력 줄 수가 다른 장비에서는 틀릴 수 있습니다."
        set_inline_status(
            self.pick_status_label,
            "warning" if suggestion.position_based else "success",
            message,
        )

    def _highlight_extracted_value(self, sample: str, value: str) -> None:
        selections = []
        if value:
            start = sample.find(value)
            if start >= 0:
                selection = QTextEdit.ExtraSelection()
                selection.format.setBackground(QColor("#fde68a"))
                cursor = self.column_output_view.textCursor()
                cursor.setPosition(start)
                cursor.setPosition(start + len(value), QTextCursor.MoveMode.KeepAnchor)
                selection.cursor = cursor
                selections.append(selection)
        self.column_output_view.setExtraSelections(selections)

    def _build_trial_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("profileTrialTab")
        self.trial_tab = tab
        layout = QVBoxLayout(tab)
        layout.addWidget(
            make_dialog_intro(
                "저장하기 전에 장비 한 대에 접속해 이 프로파일의 명령을 한 번 실행합니다. "
                "가져온 출력은 출력 예시에 채워지고, Excel 컬럼에 들어갈 값을 바로 보여 줍니다. "
                "계정과 비밀번호는 저장하지 않습니다."
            )
        )
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.trial_device_combo = NoWheelComboBox()
        self.trial_device_combo.setObjectName("profileTrialDeviceCombo")
        self.trial_device_combo.setAccessibleName("장비 목록에서 시험할 장비 고르기")
        self.trial_device_combo.setToolTip(
            "장비 점검 화면에서 고른 장비 목록의 장비입니다. 고르면 IP·포트·계정·비밀번호를 채웁니다."
        )
        self.trial_device_combo.currentIndexChanged.connect(self._fill_trial_from_inventory)
        self._populate_trial_devices()
        if self.inventory_rows:
            form.addRow("장비 목록에서 고르기", self.trial_device_combo)
        else:
            self.trial_device_combo.hide()
        self.trial_ip_edit = QLineEdit()
        self.trial_ip_edit.setObjectName("profileTrialIpEdit")
        self.trial_ip_edit.setPlaceholderText("예: 192.168.0.10")
        self.trial_ip_edit.textChanged.connect(self._refresh_required_marks)
        self.trial_port_spin = NoWheelSpinBox()
        self.trial_port_spin.setRange(1, 65535)
        self.trial_port_spin.setValue(22)
        self.trial_username_edit = QLineEdit()
        self.trial_username_edit.setPlaceholderText("예: admin")
        self.trial_password_edit = QLineEdit()
        self.trial_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.trial_enable_edit = QLineEdit()
        self.trial_enable_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.trial_enable_edit.setPlaceholderText("필요한 장비만 입력")
        form.addRow(required_label("장비 IP"), self.trial_ip_edit)
        form.addRow("포트", self.trial_port_spin)
        form.addRow("계정", self.trial_username_edit)
        form.addRow("비밀번호", self.trial_password_edit)
        form.addRow("enable 비밀번호", self.trial_enable_edit)
        layout.addLayout(form)

        self.trial_include_backup_check = make_visible_checkbox("백업 명령도 함께 실행")
        self.trial_include_backup_check.setChecked(True)
        self.trial_fill_samples_check = make_visible_checkbox(
            "가져온 출력으로 출력 예시 바꾸기"
        )
        self.trial_fill_samples_check.setChecked(True)
        options = QHBoxLayout()
        options.addWidget(self.trial_include_backup_check)
        options.addWidget(self.trial_fill_samples_check)
        options.addStretch(1)
        layout.addLayout(options)

        buttons = QHBoxLayout()
        self.trial_probe_button = make_action_button(
            "SSH 방식 확인",
            ActionKind.SECONDARY,
            tooltip="로그인하지 않고 장비가 지원하는 SSH 방식만 확인합니다.",
        )
        self.trial_probe_button.setObjectName("profileTrialProbeButton")
        self.trial_probe_button.clicked.connect(self._start_ssh_probe)
        self.trial_run_button = make_action_button(
            "시험 실행",
            ActionKind.PRIMARY,
            tooltip="장비에 로그인해 이 프로파일의 명령을 한 번 실행합니다.",
        )
        self.trial_run_button.setObjectName("profileTrialRunButton")
        self.trial_run_button.clicked.connect(self._start_profile_trial)
        self.trial_stop_button = make_action_button("중지", ActionKind.STOP)
        self.trial_stop_button.setEnabled(False)
        self.trial_stop_button.clicked.connect(self._stop_profile_trial)
        self.trial_log_button = make_action_button(
            "세션 로그 열기",
            ActionKind.OPEN,
            tooltip="장비와 주고받은 내용을 기록한 폴더를 엽니다.",
        )
        self.trial_log_button.setEnabled(False)
        self.trial_log_button.clicked.connect(self._open_trial_session_logs)
        buttons.addWidget(self.trial_probe_button)
        buttons.addWidget(self.trial_run_button)
        buttons.addWidget(self.trial_stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.trial_log_button)
        layout.addLayout(buttons)

        self.trial_status_label = make_inline_status(
            "info",
            "장비 목록에서 장비를 고르거나 IP와 계정을 입력한 뒤 시험 실행을 누르세요."
            if self.inventory_rows
            else "장비 IP와 계정을 입력한 뒤 시험 실행을 누르세요.",
        )
        self.trial_status_label.setObjectName("profileTrialStatus")
        layout.addWidget(self.trial_status_label)
        self.trial_session_view = QPlainTextEdit()
        self.trial_session_view.setObjectName("profileTrialSession")
        self.trial_session_view.setReadOnly(True)
        self.trial_session_view.setAccessibleName("장비 세션 화면")
        self.trial_session_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.trial_session_view.setMaximumBlockCount(_TRIAL_SESSION_MAX_LINES)
        self.trial_session_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.trial_session_view.setPlaceholderText(
            "시험 실행을 누르면 장비와 주고받는 CLI 화면이 여기에 실시간으로 나옵니다."
        )
        self.trial_result_view = QPlainTextEdit()
        self.trial_result_view.setObjectName("profileTrialResult")
        self.trial_result_view.setReadOnly(True)
        self.trial_result_view.setAccessibleName("장비 시험 결과")
        output_split = QSplitter(Qt.Orientation.Horizontal)
        output_split.setObjectName("profileTrialOutputSplit")
        output_split.setChildrenCollapsible(False)
        for title, view in (
            ("세션 화면 (실시간)", self.trial_session_view),
            ("시험 결과", self.trial_result_view),
        ):
            pane = QWidget()
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            pane_layout.setSpacing(4)
            pane_layout.addWidget(QLabel(title))
            pane_layout.addWidget(view, 1)
            output_split.addWidget(pane)
        output_split.setStretchFactor(0, 3)
        output_split.setStretchFactor(1, 2)
        layout.addWidget(output_split, 1)
        self.tabs.addTab(tab, "4. 장비로 시험")

    def _trial_device_label(self, row: dict[str, Any]) -> str:
        device = " / ".join(
            str(row.get(key, "")).strip()
            for key in ("vendor", "os", "model")
            if str(row.get(key, "")).strip()
        )
        return f"{row.get('ip', '')}  ({device})" if device else str(row.get("ip", ""))

    def _populate_trial_devices(self) -> None:
        self.trial_device_combo.blockSignals(True)
        self.trial_device_combo.clear()
        self.trial_device_combo.addItem("직접 입력", -1)
        for index, row in enumerate(self.inventory_rows):
            self.trial_device_combo.addItem(self._trial_device_label(row), index)
        self.trial_device_combo.blockSignals(False)

    def _sort_trial_devices(self) -> None:
        """Put devices of the profile's vendor/OS first in the picker."""
        if not self.inventory_rows:
            return
        vendor = self.vendor_edit.text().strip().casefold()
        os_name = self.os_edit.text().strip().casefold()
        current = self.trial_device_combo.currentData()
        selected = (
            self.inventory_rows[current]
            if isinstance(current, int) and 0 <= current < len(self.inventory_rows)
            else None
        )
        self.inventory_rows.sort(
            key=lambda row: (
                str(row.get("vendor", "")).strip().casefold() != vendor
                or str(row.get("os", "")).strip().casefold() != os_name
            )
        )
        self._populate_trial_devices()
        if selected is not None:
            position = self.trial_device_combo.findData(self.inventory_rows.index(selected))
            self.trial_device_combo.blockSignals(True)
            self.trial_device_combo.setCurrentIndex(max(position, 0))
            self.trial_device_combo.blockSignals(False)

    def _fill_trial_from_inventory(self, _index: int | None = None) -> None:
        index = self.trial_device_combo.currentData()
        if not isinstance(index, int) or not 0 <= index < len(self.inventory_rows):
            return
        row = self.inventory_rows[index]
        connection = str(row.get("connection_type", "") or "").strip().lower()
        if connection in {"ssh", "telnet"}:
            position = self.connection_combo.findData(connection)
            if position >= 0:
                self.connection_combo.setCurrentIndex(position)
        self.trial_ip_edit.setText(str(row.get("ip", "")).strip())
        try:
            port = int(float(str(row.get("port", "") or 0)))
        except ValueError:
            port = 0
        if 1 <= port <= 65535:
            self.trial_port_spin.setValue(port)
        self.trial_username_edit.setText(str(row.get("username", "") or ""))
        self.trial_password_edit.setText(str(row.get("password", "") or ""))
        self.trial_enable_edit.setText(str(row.get("enable_password", "") or ""))
        set_inline_status(
            self.trial_status_label,
            "info",
            f"장비 목록의 {row.get('ip', '')} 정보를 채웠습니다. 시험 실행을 누르세요.",
        )

    def _sync_trial_port(self, _index: int | None = None) -> None:
        defaults = {"ssh": 22, "telnet": 23}
        current = self.trial_port_spin.value()
        if current in defaults.values():
            self.trial_port_spin.setValue(
                defaults.get(self.connection_combo.currentData(), current)
            )
        self.trial_probe_button.setEnabled(
            not self._trial_running and self.connection_combo.currentData() == "ssh"
        )

    def _trial_commands(self) -> list[str]:
        commands = self._commands()
        backup = self.state["backup_command"]
        if (
            self.trial_include_backup_check.isChecked()
            and self.state["backup_enabled"]
            and backup
            and backup.casefold() not in {command.casefold() for command in commands}
        ):
            commands.append(backup)
        return commands

    def _set_trial_running(self, running: bool) -> None:
        self._trial_running = running
        self.trial_run_button.setEnabled(not running)
        self.trial_stop_button.setEnabled(running)
        self.trial_probe_button.setEnabled(
            not running and self.connection_combo.currentData() == "ssh"
        )
        for widget in (
            self.trial_device_combo,
            self.trial_ip_edit,
            self.trial_port_spin,
            self.trial_username_edit,
            self.trial_password_edit,
            self.trial_enable_edit,
        ):
            widget.setEnabled(not running)

    def _start_ssh_probe(self) -> None:
        host = self.trial_ip_edit.text().strip()
        if not host:
            set_inline_status(self.trial_status_label, "warning", "장비 IP를 입력하세요.")
            self.trial_ip_edit.setFocus()
            return
        self._set_trial_running(True)
        self.trial_stop_button.setEnabled(False)
        set_inline_status(
            self.trial_status_label, "info", f"{host}의 SSH 방식을 확인하는 중입니다..."
        )
        self.trial_result_view.clear()
        self._trial_runner.start(
            self.service.probe_ssh_device,
            host,
            self.trial_port_spin.value(),
            on_result=self._show_probe_result,
            on_error=self._show_probe_error,
            on_finished=lambda: self._set_trial_running(False),
        )

    def _show_probe_result(self, probe: object) -> None:
        mode = getattr(probe, "mode", "")
        kind = {"modern": "success", "legacy": "warning"}.get(mode, "error")
        set_inline_status(self.trial_status_label, kind, probe.summary())
        self.trial_result_view.setPlainText(probe.details())

    def _show_probe_error(self, text: str) -> None:
        set_inline_status(self.trial_status_label, "error", text)

    def _start_profile_trial(self) -> None:
        self._collect_state()
        host = self.trial_ip_edit.text().strip()
        problems = []
        if not self.state["vendor"] or not self.state["os"]:
            problems.append("장비 정보 탭에서 벤더와 OS를 입력하세요.")
        if not host:
            problems.append("장비 IP를 입력하세요.")
        self._trial_ip_flagged = not host
        if problems:
            self._show_missing_required = True
            self._refresh_required_marks()
        commands = self._trial_commands()
        if not commands:
            problems.append("점검 명령을 하나 이상 입력하세요.")
        if problems:
            set_inline_status(self.trial_status_label, "warning", " ".join(problems))
            return
        command_lines = "\n".join(f"  {command}" for command in commands)
        if not confirm_risky_action(
            self,
            "장비로 시험",
            f"{host}에 로그인해 다음 명령을 한 번씩 실행합니다.\n{command_lines}",
            "조회 명령만 넣었다면 장비 설정은 바뀌지 않습니다. 설정을 바꾸는 명령은 넣지 마세요.",
            "세션 로그는 결과 폴더의 session_logs에 저장됩니다.",
            confirm_text="시험 실행",
        ):
            return
        device = {
            "ip": host,
            "port": self.trial_port_spin.value(),
            "vendor": self.state["vendor"],
            "os": self.state["os"],
            "model": self.state["model"],
            "username": self.trial_username_edit.text(),
            "password": self.trial_password_edit.text(),
            "enable_password": self.trial_enable_edit.text(),
            "connection_type": self.connection_combo.currentData(),
        }
        self._trial_cancel = Event()
        self._trial_session_dir = None
        self.trial_log_button.setEnabled(False)
        self._set_trial_running(True)
        self.trial_result_view.clear()
        self.trial_session_view.clear()
        set_inline_status(
            self.trial_status_label, "info", f"{host}에 접속하는 중입니다..."
        )
        self._trial_runner.start(
            self.service.run_profile_trial,
            device,
            commands=commands,
            ssh_device_type=self.state["ssh_device_type"],
            telnet_device_type=self.state["telnet_device_type"],
            cancel_event=self._trial_cancel,
            on_progress=self._show_trial_progress,
            on_result=self._show_trial_result,
            on_error=self._show_trial_error,
            on_finished=self._finish_profile_trial,
        )

    def _show_trial_progress(self, event: object) -> None:
        if isinstance(event, dict) and event.get("type") == "session":
            self._append_session_text(str(event.get("text", "")))
            return
        message = event.get("message", "") if isinstance(event, dict) else str(event)
        if message:
            set_inline_status(self.trial_status_label, "info", message)

    def _append_session_text(self, text: str) -> None:
        if not text:
            return
        view = self.trial_session_view
        scrollbar = view.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 2
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text.replace("\r\n", "\n").replace("\r", ""))
        if follow:
            scrollbar.setValue(scrollbar.maximum())

    def _stop_profile_trial(self) -> None:
        if self._trial_cancel is not None:
            self._trial_cancel.set()
            self.trial_stop_button.setEnabled(False)
            set_inline_status(
                self.trial_status_label, "warning", "시험을 중지하는 중입니다..."
            )

    def _finish_profile_trial(self) -> None:
        self._set_trial_running(False)
        self._trial_cancel = None

    def _show_trial_error(self, text: str) -> None:
        set_inline_status(self.trial_status_label, "error", text)

    def _show_trial_result(self, result: ProfileTrialResult) -> None:
        self._trial_session_dir = result.session_log_dir
        self.trial_log_button.setEnabled(bool(result.session_log_dir))
        lines: list[str] = []
        if result.probe_summary:
            lines.extend(["[SSH 방식]", result.probe_summary])
            if result.probe_details:
                lines.append(result.probe_details)
            lines.append("")
        if not result.success:
            set_inline_status(self.trial_status_label, "error", result.error)
            lines.extend(["[실패]", result.error])
            self.trial_result_view.setPlainText("\n".join(lines))
            return

        filled = 0
        if self.trial_fill_samples_check.isChecked():
            filled = self._apply_trial_outputs(result.outputs)
        outputs = {item["command"]: item["output"] for item in result.outputs}
        missing: list[str] = []
        lines.append("[Excel 컬럼에 들어갈 값]")
        for column in self.state["columns"]:
            name = column.get("name") or "이름 없는 컬럼"
            if column.get("method") == "python":
                lines.append(f"- {name}: (Python 함수는 저장 후 점검에서 확인)")
                continue
            value = self._extract_column_value(
                column, outputs.get(column.get("command", ""), "")
            )
            if not value:
                missing.append(name)
            lines.append(f"- {name}: {value or '(값을 찾지 못함)'}")
        lines.append("")
        for item in result.outputs:
            output_lines = item["output"].splitlines()
            lines.append(f"[{item['command']}] {len(output_lines)}줄")
            lines.extend(output_lines[:_TRIAL_OUTPUT_PREVIEW_LINES])
            if len(output_lines) > _TRIAL_OUTPUT_PREVIEW_LINES:
                lines.append("…")
            lines.append("")
        self.trial_result_view.setPlainText("\n".join(lines).rstrip())
        message = f"명령 {len(result.outputs)}개를 실행했습니다."
        if filled:
            message += f" 출력 예시 {filled}개를 장비 출력으로 바꿨습니다."
        message += " 다음 단계 '5. Excel 컬럼'에서 출력의 값을 드래그해 컬럼을 만드세요."
        if missing:
            message += (
                f" 값을 찾지 못한 컬럼: {', '.join(missing)}. "
                "Excel 컬럼 탭에서 추출 방식을 고치세요."
            )
        set_inline_status(
            self.trial_status_label, "warning" if missing else "success", message
        )

    def _apply_trial_outputs(self, outputs: list[dict[str, str]]) -> int:
        self._persist_command()
        by_command = {item["command"].casefold(): item["output"] for item in outputs}
        filled = 0
        for row in self.state["commands"]:
            output = by_command.get(str(row.get("command", "")).strip().casefold())
            if output is None:
                continue
            row["sample"] = output.replace("\r\n", "\n").replace("\r", "\n")[:20_000]
            filled += 1
        self._load_command(self._selected_command_row)
        self._refresh_extraction_preview()
        self._schedule_preview()
        return filled

    def _open_trial_session_logs(self) -> None:
        if self._trial_session_dir:
            os.startfile(self._trial_session_dir)

    def _build_review_tab(self) -> None:
        tab = QWidget()
        self.review_tab = tab
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("입력 확인"))
        self.issue_list = QListWidget()
        self.issue_list.setAccessibleName("프로파일 입력 확인")
        layout.addWidget(self.issue_list, 1)
        layout.addWidget(QLabel("프로파일 요약"))
        self.summary_preview = QPlainTextEdit()
        self.summary_preview.setReadOnly(True)
        self.summary_preview.setAccessibleName("프로파일 요약")
        layout.addWidget(self.summary_preview, 2)
        self.tabs.addTab(tab, "6. 확인·저장")

    def _build_advanced_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel(
            "전문가 설정입니다. 정규식, Python 추출 함수, YAML 원문, 접속 문제 해결 설정이 필요할 때만 사용하세요."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        create_parser_button = make_action_button(
            "Python 함수", ActionKind.ADD, tooltip="Python 추출 함수를 만듭니다."
        )
        create_parser_button.clicked.connect(self._open_python_parser_dialog)
        open_parser_button = make_action_button(
            "함수 폴더", ActionKind.OPEN, tooltip="Python 추출 함수 폴더를 엽니다."
        )
        open_parser_button.clicked.connect(
            lambda: os.startfile(str(self.service.custom_parsers_dir))
        )
        export_button = make_action_button("YAML 저장", ActionKind.EXPORT)
        export_button.clicked.connect(self._export_yaml)
        generate_button = make_action_button(
            "기본 파일", ActionKind.ADD, tooltip="기본 프로파일 파일을 생성합니다."
        )
        generate_button.clicked.connect(self._generate_all_profiles)
        actions.addWidget(create_parser_button)
        actions.addWidget(open_parser_button)
        actions.addWidget(export_button)
        actions.addWidget(generate_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.ssh_device_type_edit = QLineEdit()
        self.ssh_device_type_edit.setPlaceholderText("예: cisco_ios")
        self.telnet_device_type_edit = QLineEdit()
        self.telnet_device_type_edit.setPlaceholderText("예: cisco_ios_telnet")
        form.addRow("SSH 접속 장비 유형", self.ssh_device_type_edit)
        form.addRow("Telnet 접속 장비 유형", self.telnet_device_type_edit)
        layout.addLayout(form)
        layout.addWidget(QLabel("프로파일 YAML 미리보기"))
        self.yaml_preview = QPlainTextEdit()
        self.yaml_preview.setAccessibleName("프로파일 YAML 미리보기")
        layout.addWidget(self.yaml_preview, 1)
        self.tabs.addTab(tab, "전문가 설정")
        self.ssh_device_type_edit.textChanged.connect(self._schedule_preview)
        self.telnet_device_type_edit.textChanged.connect(self._schedule_preview)

    def _load_profile_choices(self) -> None:
        self.copy_profile_combo.blockSignals(True)
        self.copy_profile_combo.clear()
        self.copy_profile_combo.addItem("새 프로파일", None)
        for profile in self.profiles:
            label = (
                profile.get("display_name") or f"{profile['vendor']} / {profile['os']}"
            )
            if profile.get("is_reference") and "참고용" not in label:
                label = f"{label} (참고용)"
            self.copy_profile_combo.addItem(label, profile)
        self.copy_profile_combo.blockSignals(False)
        self._refresh_python_parser_choices()

    def _refresh_python_parser_choices(self, selected: str = "") -> None:
        current = (
            selected or self.python_parser_combo.currentText()
            if hasattr(self, "python_parser_combo")
            else ""
        )
        self.python_parser_combo.blockSignals(True)
        self.python_parser_combo.clear()
        self.python_parser_combo.addItem("")
        for name in self.service.available_custom_parsers():
            self.python_parser_combo.addItem(name)
        if current:
            self.python_parser_combo.setCurrentText(current)
        self.python_parser_combo.blockSignals(False)

    def _load_state(self) -> None:
        was_loading = self._loading_state
        self._loading_state = True
        try:
            scope_index = self.profile_scope_combo.findData(
                self.state.get("profile_scope", "base")
            )
            self.profile_scope_combo.setCurrentIndex(max(0, scope_index))
            self.vendor_edit.setText(self.state["vendor"])
            self.model_edit.setText(self.state["model"])
            self.os_edit.setText(self.state["os"])
            self.os_version_edit.setText(self.state["os_version"])
            connection_index = self.connection_combo.findData(
                self.state.get("connection_type", "ssh")
            )
            self.connection_combo.setCurrentIndex(max(0, connection_index))
            self.backup_enabled_check.setChecked(bool(self.state["backup_enabled"]))
            self.backup_command_edit.setText(self.state["backup_command"])
            self.ssh_device_type_edit.setText(self.state["ssh_device_type"])
            self.telnet_device_type_edit.setText(self.state["telnet_device_type"])
            self._refresh_command_list(0)
            self._refresh_column_commands()
            self._refresh_column_list(0)
        finally:
            self._loading_state = was_loading

    def _collect_state(self) -> None:
        self._persist_command()
        self._persist_column()
        self.state["profile_scope"] = self.profile_scope_combo.currentData()
        self.state["vendor"] = self.vendor_edit.text().strip()
        self.state["model"] = self.model_edit.text().strip()
        self.state["os"] = self.os_edit.text().strip()
        self.state["os_version"] = self.os_version_edit.text().strip()
        self.state["connection_type"] = self.connection_combo.currentData()
        self.state["backup_enabled"] = self.backup_enabled_check.isChecked()
        self.state["backup_command"] = self.backup_command_edit.text().strip()
        self.state["ssh_device_type"] = self.ssh_device_type_edit.text().strip()
        self.state["telnet_device_type"] = self.telnet_device_type_edit.text().strip()

    def _refresh_command_list(self, selected: int | None = None) -> None:
        self.command_list.blockSignals(True)
        self.command_list.clear()
        for index, row in enumerate(self.state["commands"], start=1):
            command = row.get("command") or f"명령 {index}"
            self.command_list.addItem(QListWidgetItem(command))
        target = min(max(selected or 0, 0), self.command_list.count() - 1)
        if self.command_list.count():
            self.command_list.setCurrentRow(target)
        self.command_list.blockSignals(False)
        self._selected_command_row = target if self.command_list.count() else -1
        self._load_command(self._selected_command_row)
        self._refresh_column_commands()

    def _load_command(self, row: int) -> None:
        self._loading_command = True
        try:
            if row < 0:
                self.command_edit.clear()
                self.sample_output_edit.clear()
                return
            data = self.state["commands"][row]
            self.command_edit.setText(data.get("command", ""))
            self.sample_output_edit.setPlainText(data.get("sample", ""))
        finally:
            self._loading_command = False

    def _persist_command(self) -> None:
        row = self._selected_command_row
        if self._loading_command or row < 0 or row >= len(self.state["commands"]):
            return
        self.state["commands"][row] = {
            "command": self.command_edit.text().strip(),
            "sample": self.sample_output_edit.toPlainText(),
        }

    def _on_command_selected(self, row: int) -> None:
        self._persist_command()
        self._selected_command_row = row
        self._load_command(row)
        self._refresh_column_commands()
        self._schedule_preview()

    def _on_command_changed(self) -> None:
        if self._loading_command:
            return
        self._persist_command()
        item = self.command_list.item(self._selected_command_row)
        if item:
            item.setText(
                self.command_edit.text().strip()
                or f"명령 {self._selected_command_row + 1}"
            )
        self._refresh_column_commands()
        self._schedule_preview()

    def _add_command(self) -> None:
        self._persist_command()
        self.state["commands"].append({"command": "show version", "sample": ""})
        self._refresh_command_list(len(self.state["commands"]) - 1)
        self._schedule_preview()

    def _remove_command(self) -> None:
        row = self.command_list.currentRow()
        if row < 0:
            return
        self._persist_command()
        command = str(self.state["commands"][row].get("command", "")).strip()
        referenced_columns = [
            str(column.get("name", "") or "이름 없는 컬럼")
            for column in self.state["columns"]
            if str(column.get("command", "")).strip() == command
        ]
        if referenced_columns:
            QMessageBox.warning(
                self,
                "명령을 삭제할 수 없음",
                "이 명령을 사용하는 Excel 컬럼이 있습니다.\n"
                f"먼저 컬럼의 명령을 변경하거나 삭제하세요: {', '.join(referenced_columns)}",
            )
            return
        self.state["commands"].pop(row)
        if not self.state["commands"]:
            self.state["commands"].append({"command": "show version", "sample": ""})
        self._refresh_command_list(max(0, row - 1))
        self._schedule_preview()

    def _refresh_column_list(self, selected: int | None = None) -> None:
        self.column_list.blockSignals(True)
        self.column_list.clear()
        for index, row in enumerate(self.state["columns"], start=1):
            name = row.get("name") or f"컬럼 {index}"
            self.column_list.addItem(QListWidgetItem(name))
        target = min(max(selected or 0, 0), self.column_list.count() - 1)
        if self.column_list.count():
            self.column_list.setCurrentRow(target)
        self.column_list.blockSignals(False)
        self._selected_column_row = target if self.column_list.count() else -1
        self._load_column(self._selected_column_row)

    def _refresh_column_commands(self) -> None:
        if not hasattr(self, "column_command_combo"):
            return
        current = self.column_command_combo.currentText()
        self.column_command_combo.blockSignals(True)
        self.column_command_combo.clear()
        for command in self._commands():
            self.column_command_combo.addItem(command)
        if current:
            index = self.column_command_combo.findText(current)
            if index >= 0:
                self.column_command_combo.setCurrentIndex(index)
        self.column_command_combo.blockSignals(False)

    def _load_column(self, row: int) -> None:
        self._loading_column = True
        try:
            if row < 0:
                self.column_name_edit.clear()
                return
            data = self.state["columns"][row]
            self.column_name_edit.setText(data.get("name", ""))
            self.column_command_combo.setCurrentText(data.get("command", ""))
            self.extract_method_combo.setCurrentIndex(
                max(
                    0,
                    self.extract_method_combo.findData(
                        data.get("method", "split_fields")
                    ),
                )
            )
            self.line_number_spin.setValue(int(data.get("line_number", 1) or 1))
            self.start_field_spin.setValue(int(data.get("start_field", 1) or 1))
            self.end_field_spin.setValue(
                int(data.get("end_field", data.get("start_field", 1)) or 1)
            )
            self.keyword_edit.setText(data.get("keyword", ""))
            self.regex_edit.setText(data.get("regex", ""))
            self.python_parser_combo.setCurrentText(data.get("python_parser", ""))
        finally:
            self._loading_column = False
        self._update_extraction_method_fields()
        self._refresh_extraction_preview()

    def _persist_column(self) -> None:
        row = self._selected_column_row
        if self._loading_column or row < 0 or row >= len(self.state["columns"]):
            return
        self.state["columns"][row] = {
            "name": self.column_name_edit.text().strip(),
            "command": self.column_command_combo.currentText().strip(),
            "method": self.extract_method_combo.currentData(),
            "line_number": self.line_number_spin.value(),
            "start_field": self.start_field_spin.value(),
            "end_field": self.end_field_spin.value(),
            "keyword": self.keyword_edit.text().strip(),
            "regex": self.regex_edit.text().strip(),
            "python_parser": self.python_parser_combo.currentText().strip(),
        }

    def _on_column_selected(self, row: int) -> None:
        self._persist_column()
        self._selected_column_row = row
        self._set_suggestions([])
        set_inline_status(self.pick_status_label, "info", "")
        self._load_column(row)
        self._schedule_preview()

    def _on_column_changed(self) -> None:
        if self._loading_column:
            return
        self._persist_column()
        item = self.column_list.item(self._selected_column_row)
        if item:
            item.setText(
                self.column_name_edit.text().strip()
                or f"컬럼 {self._selected_column_row + 1}"
            )
        self._refresh_extraction_preview()
        self._schedule_preview()

    def _on_extraction_method_changed(self) -> None:
        self._update_extraction_method_fields()
        self._on_column_changed()

    def _update_extraction_method_fields(self) -> None:
        if not hasattr(self, "_extraction_method_fields"):
            return
        method = self.extract_method_combo.currentData() or "split_fields"
        visible_fields = {
            "split_fields": {"line_number", "start_field", "end_field"},
            "keyword_after": {"keyword"},
            "line_text": {"line_number"},
            "regex": {"regex"},
            "python": {"python"},
        }.get(str(method), set())
        for name, (label, field) in self._extraction_method_fields.items():
            label.setVisible(name in visible_fields)
            field.setVisible(name in visible_fields)

    def _add_column(self) -> None:
        self._persist_column()
        command = self._commands()[0] if self._commands() else ""
        self.state["columns"].append(
            {
                "name": "새 컬럼",
                "command": command,
                "method": "split_fields",
                "line_number": 1,
                "start_field": 1,
                "end_field": 1,
                "keyword": "",
                "regex": "",
                "python_parser": "",
            }
        )
        self._refresh_column_list(len(self.state["columns"]) - 1)
        self._schedule_preview()

    def _remove_column(self) -> None:
        row = self.column_list.currentRow()
        if row < 0:
            return
        self.state["columns"].pop(row)
        if not self.state["columns"]:
            self._add_column()
            return
        self._refresh_column_list(max(0, row - 1))
        self._schedule_preview()

    def _commands(self) -> list[str]:
        return [
            row.get("command", "").strip()
            for row in self.state["commands"]
            if row.get("command", "").strip()
        ]

    def _sample_for_command(self, command: str) -> str:
        for row in self.state["commands"]:
            if row.get("command") == command:
                return row.get("sample", "")
        return ""

    def _refresh_extraction_preview(self) -> None:
        command = self.column_command_combo.currentText().strip()
        sample = self._sample_for_command(command)
        lines = sample.splitlines()
        numbered = []
        for idx, line in enumerate(lines, start=1):
            marker = ">" if idx == self.line_number_spin.value() else " "
            fields = " | ".join(
                f"{i}:{value}" for i, value in enumerate(line.split(), start=1)
            )
            numbered.append(f"{marker} {idx:02d}: {line}\n     {fields}")
        self.sample_line_view.setPlainText("\n".join(numbered))
        if self.column_output_view.toPlainText() != sample:
            self.column_output_view.setPlainText(sample)
        result = self._preview_value(sample)
        self.preview_label.setText(f"추출 결과: {result or '-'}")
        self._highlight_extracted_value(sample, result)

    def _preview_value(self, sample: str) -> str:
        return self._extract_column_value(
            {
                "method": self.extract_method_combo.currentData(),
                "line_number": self.line_number_spin.value(),
                "start_field": self.start_field_spin.value(),
                "end_field": self.end_field_spin.value(),
                "keyword": self.keyword_edit.text(),
                "regex": self.regex_edit.text(),
            },
            sample,
        )

    @staticmethod
    def _extract_column_value(column: dict[str, Any], sample: str) -> str:
        return extract_value(column, sample)

    def _copy_selected_profile(self, index: int | None = None) -> None:
        if self._loading_state:
            return
        selected_index = (
            self.copy_profile_combo.currentIndex() if index is None else int(index)
        )
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "작성 중인 내용 바꾸기",
                "저장하지 않은 변경 내용이 있습니다. 선택한 프로파일로 바꿀까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.copy_profile_combo.blockSignals(True)
                self.copy_profile_combo.setCurrentIndex(self._last_profile_index)
                self.copy_profile_combo.blockSignals(False)
                return
        profile = self.copy_profile_combo.currentData()
        if isinstance(profile, dict):
            self.state["profile_scope"] = profile.get("profile_scope", "base")
            self.state["vendor"] = profile.get("vendor", "")
            self.state["model"] = profile.get("model", "")
            self.state["os"] = profile.get("os", "")
            self.state["os_version"] = profile.get("os_version", "")
            self.state["commands"] = [
                {"command": command, "sample": ""}
                for command in profile.get("commands", [])
            ] or self.state["commands"]
            self.state["backup_command"] = profile.get("backup_command", "")
            self.state["backup_enabled"] = bool(profile.get("backup_command"))
            self.state["columns"] = self._columns_from_profile(profile)
            connection = profile.get("connection_overrides") or {}
            self.state["ssh_device_type"] = (
                connection.get("ssh") or connection.get("default") or ""
            )
            self.state["telnet_device_type"] = connection.get("telnet") or ""
        else:
            self.state = self._empty_state()
        self._load_state()
        self._last_profile_index = selected_index
        self._dirty = True
        self._schedule_preview()

    def _columns_from_profile(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for command, rule in (profile.get("parsing_rules") or {}).items():
            if not isinstance(rule, dict):
                continue
            rule_list = (
                rule.get("patterns")
                if isinstance(rule.get("patterns"), list)
                else [rule]
            )
            for item in rule_list:
                if not isinstance(item, dict):
                    continue
                if item.get("parser_type") in {
                    "split_fields",
                    "keyword_after",
                    "line_text",
                }:
                    rows.append(
                        {
                            "name": item.get("output_column", ""),
                            "command": command,
                            "method": item.get("parser_type", "split_fields"),
                            "line_number": int(item.get("line_number", 1) or 1),
                            "start_field": int(item.get("start_field", 1) or 1),
                            "end_field": int(
                                item.get("end_field", item.get("start_field", 1)) or 1
                            ),
                            "keyword": item.get("keyword", ""),
                            "regex": "",
                            "python_parser": "",
                        }
                    )
                elif item.get("pattern") and item.get("output_column"):
                    rows.append(
                        {
                            "name": item.get("output_column", ""),
                            "command": command,
                            "method": "regex",
                            "line_number": 1,
                            "start_field": 1,
                            "end_field": 1,
                            "keyword": "",
                            "regex": item.get("pattern", ""),
                            "python_parser": "",
                        }
                    )
                elif item.get("custom_parser"):
                    rows.append(
                        {
                            "name": item.get("output_column", command),
                            "command": command,
                            "method": "python",
                            "line_number": 1,
                            "start_field": 1,
                            "end_field": 1,
                            "keyword": "",
                            "regex": "",
                            "python_parser": item.get("custom_parser", ""),
                        }
                    )
        return rows or self.state["columns"]

    def refresh_preview(self) -> None:
        self._collect_state()
        self._refresh_required_marks()
        issues = self._validation_issues()
        parser_rows = self._parser_rows_from_columns()
        try:
            self.latest_yaml_text = self.service.build_simple_custom_rules_yaml(
                vendor=self.state["vendor"],
                os_name=self.state["os"],
                inspection_commands=self._commands(),
                backup_command=self.state["backup_command"]
                if self.state["backup_enabled"]
                else "",
                default_device_type=self.state["ssh_device_type"],
                telnet_device_type=self.state["telnet_device_type"],
                parser_rows=parser_rows,
                model=self.state["model"],
                os_version=self.state["os_version"],
                output_columns=[row.get("name", "") for row in self.state["columns"]],
                model_specific=self.state.get("profile_scope") == "model",
            )
        except Exception as exc:
            issues.append(str(exc))
            self.latest_yaml_text = "# 입력을 완성하면 프로파일 YAML이 생성됩니다.\n"

        self.issue_list.clear()
        if issues:
            for issue in issues:
                item = QListWidgetItem(issue)
                item.setBackground(ERROR_BG)
                self.issue_list.addItem(item)
        else:
            item = QListWidgetItem("저장 가능한 상태입니다.")
            item.setBackground(OK_BG)
            self.issue_list.addItem(item)
        self.summary_preview.setPlainText(self._human_summary(parser_rows, issues))
        self.yaml_preview.setPlainText(self.latest_yaml_text)
        self.save_button.setEnabled(not issues)

    def _parser_rows_from_columns(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for column in self.state["columns"]:
            method = column.get("method", "split_fields")
            rows.append(
                {
                    "command": column.get("command", ""),
                    "output_column": column.get("name", ""),
                    "parser_type": method
                    if method in {"split_fields", "keyword_after", "line_text"}
                    else "",
                    "line_number": column.get("line_number", 1),
                    "start_field": column.get("start_field", 1),
                    "end_field": column.get("end_field", column.get("start_field", 1)),
                    "keyword": column.get("keyword", ""),
                    "pattern": column.get("regex", "") if method == "regex" else "",
                    "custom_parser": column.get("python_parser", "")
                    if method == "python"
                    else "",
                }
            )
        return rows

    def _validation_issues(self) -> list[str]:
        issues: list[str] = []
        if not self.state["vendor"]:
            issues.append("벤더를 입력하세요. 예: Cisco")
        if not self.state["os"]:
            issues.append("OS를 입력하세요. 예: IOS-XE")
        if self.state.get("profile_scope") == "model" and not self.state["model"]:
            issues.append("모델 전용 프로파일에는 장비 모델을 입력하세요.")
        commands = self._commands()
        if not commands:
            issues.append("점검 명령을 하나 이상 입력하세요. 예: show version")
        if len(self.state["commands"]) > 64:
            issues.append("점검 명령은 최대 64개까지 저장할 수 있습니다.")
        seen_commands: set[str] = set()
        for index, row in enumerate(self.state["commands"], start=1):
            command = str(row.get("command", "")).strip()
            sample = str(row.get("sample", ""))
            if not command:
                issues.append(f"점검 명령 {index}이 비어 있습니다.")
                continue
            if len(command) > 512 or _has_unsafe_single_line_character(command):
                issues.append(f"점검 명령 {index}은 제어 문자 없는 512자 이하 한 줄이어야 합니다.")
            normalized_command = command.casefold()
            if normalized_command in seen_commands:
                issues.append(f"점검 명령이 중복되었습니다: {command}")
            seen_commands.add(normalized_command)
            if len(sample) > 20_000:
                issues.append(f"점검 명령 {index}의 출력 예시는 20,000자 이하여야 합니다.")
        if self.state["backup_enabled"] and not self.state["backup_command"]:
            issues.append("장비 구성 백업을 사용하려면 백업 명령을 입력하세요.")
        if self.state["backup_command"] and (
            len(self.state["backup_command"]) > 512
            or _has_unsafe_single_line_character(self.state["backup_command"])
        ):
            issues.append("백업 명령은 제어 문자 없는 512자 이하 한 줄이어야 합니다.")
        supported_device_types = _supported_netmiko_device_types()
        for label, device_type in (
            ("SSH", self.state["ssh_device_type"]),
            ("Telnet", self.state["telnet_device_type"]),
        ):
            if not device_type:
                continue
            if not _DEVICE_TYPE_RE.fullmatch(device_type):
                issues.append(f"{label} Netmiko 장비 유형 형식이 올바르지 않습니다: {device_type}")
            elif supported_device_types is None:
                issues.append(f"{label} Netmiko 장비 유형 지원 여부를 확인할 수 없습니다.")
            elif device_type not in supported_device_types:
                issues.append(f"지원되지 않는 {label} Netmiko 장비 유형입니다: {device_type}")
        if len(self.state["columns"]) > 64:
            issues.append("Excel 출력 컬럼은 최대 64개까지 저장할 수 있습니다.")
        seen_columns: set[str] = set()
        for column in self.state["columns"]:
            column_name = str(column.get("name", "")).strip()
            if not column_name:
                issues.append("Excel 컬럼명을 입력하세요. 예: OS버전")
            elif len(column_name) > 100 or _has_unsafe_single_line_character(column_name):
                issues.append(f"Excel 컬럼명은 제어 문자 없는 100자 이하 한 줄이어야 합니다: {column_name}")
            elif column_name.casefold() in seen_columns:
                issues.append(f"Excel 컬럼명이 중복되었습니다: {column_name}")
            else:
                seen_columns.add(column_name.casefold())
            referenced_command = str(column.get("command", "")).strip()
            if not referenced_command:
                issues.append(
                    f"{column.get('name') or '컬럼'}: 가져올 명령 결과를 선택하세요."
                )
            elif referenced_command.casefold() not in seen_commands:
                issues.append(
                    f"{column.get('name') or '컬럼'}: 점검 명령 목록에 없는 명령을 참조합니다."
                )
            if column.get("method") == "keyword_after" and not column.get("keyword"):
                issues.append(
                    f"{column.get('name')}: 찾을 단어를 입력하세요. 예: Processor board ID"
                )
            if column.get("method") == "regex":
                pattern = str(column.get("regex", "")).strip()
                if not pattern:
                    issues.append(f"{column.get('name')}: 정규식을 입력하세요.")
                else:
                    try:
                        compiled = re.compile(pattern)
                    except re.error as exc:
                        issues.append(
                            f"{column.get('name')}: 정규식 오류를 수정하세요. ({exc})"
                        )
                    else:
                        if compiled.groups < 1:
                            issues.append(
                                f"{column.get('name')}: 정규식에는 값을 가져올 캡처 그룹 (...)이 필요합니다."
                            )
            if column.get("method") == "python" and not column.get("python_parser"):
                issues.append(
                    f"{column.get('name')}: Python 추출 함수를 선택하거나 만드세요."
                )
        return issues

    def _human_summary(
        self, parser_rows: list[dict[str, Any]], issues: list[str]
    ) -> str:
        lines = [
            "적용 범위: "
            + (
                "특정 장비 모델 전용"
                if self.state.get("profile_scope") == "model"
                else "벤더/OS 공통"
            ),
            f"장비: {self.state['vendor']} {self.state['model']} / {self.state['os']} {self.state['os_version']}".strip(),
            f"접속: {self.connection_combo.currentText()}",
            "",
            "[실행할 명령]",
            *[f"- {command}" for command in self._commands()],
            "",
            "[Excel 컬럼]",
        ]
        for row in parser_rows:
            method = row.get("parser_type") or (
                "정규식" if row.get("pattern") else "Python"
            )
            lines.append(
                f"- {row.get('output_column')} <- {row.get('command')} ({method})"
            )
        if issues:
            lines.extend(["", "[저장 전 확인]", *[f"- {issue}" for issue in issues]])
        return "\n".join(lines)

    def _save_profile(self) -> None:
        self._show_missing_required = True
        self.refresh_preview()
        if not self.save_button.isEnabled():
            self.tabs.setCurrentWidget(self.review_tab)
            return
        vendor = self.state["vendor"]
        os_name = self.state["os"]
        model_specific = self.state.get("profile_scope") == "model"
        model = self.state["model"] if model_specific else None
        if self.service.custom_profile_exists(vendor, os_name, model):
            target = (
                f"{vendor} / {os_name} / {self.state['model']}"
                if model_specific
                else f"{vendor} / {os_name}"
            )
            answer = QMessageBox.question(
                self,
                "기존 프로파일 덮어쓰기",
                f"{target} 사용자 프로파일이 이미 있습니다.\n"
                "이 프로파일만 새 내용으로 바꿀까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            path = self.service.merge_custom_profile_rules_text(self.latest_yaml_text)
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return
        self._dirty = False
        QMessageBox.information(
            self, "프로파일 저장 완료", f"프로파일을 저장했습니다.\n{path}"
        )

    def _open_python_parser_dialog(self) -> None:
        self._parser_dialog = PythonParserDialog(self.service, self)
        if self._parser_dialog.exec() and self._parser_dialog.saved_function_name:
            function_name = self._parser_dialog.saved_function_name
            self._refresh_python_parser_choices(function_name)
            self.extract_method_combo.setCurrentIndex(
                self.extract_method_combo.findData("python")
            )
            self.python_parser_combo.setCurrentText(function_name)
            self._on_column_changed()

    def _export_yaml(self) -> Path | None:
        suggested_path = timestamped_export_path(
            self.exports_dir, "custom_rules", "yaml"
        )
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "YAML 내보내기",
            str(suggested_path),
            "YAML Files (*.yaml *.yml)",
        )
        if not selected_path:
            return None
        path = Path(selected_path)
        if path.suffix.casefold() not in {".yaml", ".yml"}:
            path = (
                path.with_suffix(".yaml")
                if path.suffix
                else Path(f"{path}.yaml")
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.yaml_preview.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self, "내보내기 실패", f"YAML을 저장하지 못했습니다.\n{exc}"
            )
            return None
        QMessageBox.information(
            self, "내보내기 완료", f"YAML을 저장했습니다:\n{path}"
        )
        return path

    def _generate_all_profiles(self) -> None:
        try:
            count = self.service.ensure_vendor_profile_files()
        except Exception as exc:
            QMessageBox.warning(self, "프로파일 생성 실패", str(exc))
            return
        QMessageBox.information(
            self,
            "프로파일 생성 완료",
            f"{count}개 기본 프로파일 파일을 생성했습니다.\n{self.service.vendor_profiles_dir}",
        )
        os.startfile(str(self.service.vendor_profiles_dir))

    def _schedule_preview(self) -> None:
        if self._loading_state:
            return
        self._dirty = True
        self.preview_timer.start()

    def _request_close(self) -> None:
        if self._trial_still_running():
            return
        if self._confirm_discard_changes():
            self.accept()

    def _trial_still_running(self) -> bool:
        if not self._trial_running:
            return False
        self._stop_profile_trial()
        QMessageBox.information(
            self,
            "장비 시험 중",
            "장비 시험을 중지하고 있습니다. 끝난 뒤 다시 닫아 주세요.",
        )
        return True

    def _confirm_discard_changes(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "저장하지 않은 변경 내용",
            "저장하지 않은 변경 내용이 있습니다. 닫을까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        if self.isVisible() and self._trial_still_running():
            event.ignore()
            return
        if not self.isVisible() or self._confirm_discard_changes():
            event.accept()
            return
        event.ignore()
