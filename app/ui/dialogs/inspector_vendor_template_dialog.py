from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.common import make_dialog_intro, polish_dialog
from netops_suite.modules.inspector import InspectorService


from netops_suite.ui.actions import ActionKind, make_action_button

ERROR_BG = QColor("#fff1ed")
OK_BG = QColor("#eef8eb")


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
            "background:transparent; color:#9a3412; border:0; "
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
            "        if \"CPU Usage\" in line:\n"
            "            parts = line.split()\n"
            "            return \" \".join(parts[-2:])\n"
            "    return \"\"\n"
        )
        layout.addWidget(QLabel("Python 코드"))
        layout.addWidget(self.code_edit, 3)

        self.sample_output_edit = QPlainTextEdit()
        self.sample_output_edit.setPlainText("CPU Usage        12 %\nMemory Usage     40 %")
        layout.addWidget(QLabel("테스트용 출력 예시"))
        layout.addWidget(self.sample_output_edit, 1)

        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMaximumHeight(90)
        layout.addWidget(QLabel("테스트 결과"))
        layout.addWidget(self.result_view)

        actions = QHBoxLayout()
        test_button = make_action_button("테스트", ActionKind.START, tooltip="작성한 Python 추출 함수를 테스트합니다.")
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
        QMessageBox.information(self, "저장 완료", f"Python 추출 함수를 저장했습니다.\n{path}")
        self.accept()


class InspectorVendorTemplateDialog(QDialog):
    def __init__(self, service: InspectorService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.templates = service.supported_profile_templates()
        self.state = self._empty_state()
        self.latest_yaml_text = ""
        self._selected_command_row = -1
        self._selected_column_row = -1
        self._loading_command = False
        self._loading_column = False
        self._parser_dialog: PythonParserDialog | None = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(180)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.refresh_preview)

        self.setWindowTitle("장비 점검 템플릿 만들기")
        self.resize(1120, 780)
        self._build_ui()
        self._load_template_choices()
        self._load_state()
        self.refresh_preview()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
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
        intro = make_dialog_intro(
            "이 템플릿은 장비에 명령어를 실행하고, 출력값을 Excel 컬럼으로 정리합니다. "
            "명령어 출력 예시를 붙여넣고, Excel에 넣을 값을 선택하세요."
        )
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        self._build_device_tab()
        self._build_command_tab()
        self._build_column_tab()
        self._build_review_tab()
        self._build_advanced_tab()

        actions = QHBoxLayout()
        refresh_button = make_action_button("갱신", ActionKind.REFRESH, tooltip="YAML 미리보기를 갱신합니다.")
        refresh_button.clicked.connect(self.refresh_preview)
        self.save_button = make_action_button("저장", ActionKind.SAVE, tooltip="템플릿을 저장합니다.")
        self.save_button.clicked.connect(self._save_template)
        close_button = make_action_button("닫기", ActionKind.CANCEL)
        close_button.clicked.connect(self.accept)
        actions.addStretch(1)
        actions.addWidget(refresh_button)
        actions.addWidget(self.save_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def _build_device_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel("어떤 장비의 점검 결과를 Excel로 정리할지 입력합니다.")
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
        self.connection_combo = QComboBox()
        self.connection_combo.addItem("SSH", "ssh")
        self.connection_combo.addItem("Telnet", "telnet")
        self.copy_template_combo = QComboBox()
        self.copy_template_combo.currentIndexChanged.connect(self._copy_selected_template)
        form.addRow("벤더", self.vendor_edit)
        form.addRow("모델", self.model_edit)
        form.addRow("OS", self.os_edit)
        form.addRow("OS 버전", self.os_version_edit)
        form.addRow("접속 방식", self.connection_combo)
        form.addRow("기존 템플릿 복사", self.copy_template_combo)
        layout.addLayout(form)
        layout.addStretch(1)
        self.tabs.addTab(tab, "장비 정보")

        for widget in (self.vendor_edit, self.model_edit, self.os_edit, self.os_version_edit):
            widget.textChanged.connect(self._schedule_preview)
        self.connection_combo.currentIndexChanged.connect(self._schedule_preview)

    def _build_command_tab(self) -> None:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        left = QVBoxLayout()
        left.addWidget(QLabel("장비에서 실행할 점검 명령어"))
        self.command_list = QListWidget()
        self.command_list.currentRowChanged.connect(self._on_command_selected)
        left.addWidget(self.command_list, 1)
        buttons = QHBoxLayout()
        add_button = make_action_button("추가", ActionKind.ADD, tooltip="점검 명령을 추가합니다.")
        add_button.clicked.connect(self._add_command)
        remove_button = make_action_button("삭제", ActionKind.DELETE, tooltip="선택한 점검 명령을 삭제합니다.")
        remove_button.clicked.connect(self._remove_command)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        left.addLayout(buttons)

        right = QVBoxLayout()
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("예: show version")
        self.sample_output_edit = QPlainTextEdit()
        self.sample_output_edit.setPlaceholderText(
            "예:\nCisco IOS XE Software, Version 17.09.04\ncisco C9300-24T processor\nProcessor board ID FOC1234ABCD"
        )
        form.addRow("명령어", self.command_edit)
        form.addRow("출력 예시", self.sample_output_edit)
        right.addLayout(form)
        self.backup_enabled_check = QCheckBox("백업 명령도 저장")
        self.backup_command_edit = QLineEdit()
        self.backup_command_edit.setPlaceholderText("예: show running-config, show configuration, display current-configuration")
        right.addWidget(self.backup_enabled_check)
        right.addWidget(self.backup_command_edit)
        right.addStretch(1)

        layout.addLayout(left, 2)
        layout.addLayout(right, 4)
        self.tabs.addTab(tab, "점검 명령")

        self.command_edit.textChanged.connect(self._on_command_changed)
        self.sample_output_edit.textChanged.connect(self._on_command_changed)
        self.backup_enabled_check.toggled.connect(self._schedule_preview)
        self.backup_command_edit.textChanged.connect(self._schedule_preview)

    def _build_column_tab(self) -> None:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        left = QVBoxLayout()
        left.addWidget(QLabel("Excel에 표시할 컬럼"))
        self.column_list = QListWidget()
        self.column_list.currentRowChanged.connect(self._on_column_selected)
        left.addWidget(self.column_list, 1)
        buttons = QHBoxLayout()
        add_button = make_action_button("추가", ActionKind.ADD, tooltip="Excel 출력 컬럼을 추가합니다.")
        add_button.clicked.connect(self._add_column)
        remove_button = make_action_button("삭제", ActionKind.DELETE, tooltip="선택한 출력 컬럼을 삭제합니다.")
        remove_button.clicked.connect(self._remove_column)
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        left.addLayout(buttons)

        right = QVBoxLayout()
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.column_name_edit = QLineEdit()
        self.column_name_edit.setPlaceholderText("예: OS버전, 시리얼번호, CPU 사용률")
        self.column_command_combo = QComboBox()
        self.extract_method_combo = QComboBox()
        self.extract_method_combo.addItem("몇 번째 줄/몇 번째 값 가져오기", "split_fields")
        self.extract_method_combo.addItem("특정 단어 뒤의 값 가져오기", "keyword_after")
        self.extract_method_combo.addItem("줄 전체 가져오기", "line_text")
        self.extract_method_combo.addItem("정규식 직접 입력", "regex")
        self.extract_method_combo.addItem("Python 고급 추출", "python")
        self.line_number_spin = QSpinBox()
        self.line_number_spin.setRange(1, 10000)
        self.start_field_spin = QSpinBox()
        self.start_field_spin.setRange(1, 1000)
        self.end_field_spin = QSpinBox()
        self.end_field_spin.setRange(1, 1000)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("예: Processor board ID")
        self.regex_edit = QLineEdit()
        self.regex_edit.setPlaceholderText(r"예: Version\s+([0-9.]+)")
        self.python_parser_combo = QComboBox()
        self.python_parser_combo.setEditable(True)
        self.preview_label = QLabel("추출 결과: -")
        self.preview_label.setWordWrap(True)
        form.addRow("Excel 컬럼명", self.column_name_edit)
        form.addRow("가져올 명령 결과", self.column_command_combo)
        form.addRow("추출 방식", self.extract_method_combo)
        form.addRow("줄 번호", self.line_number_spin)
        form.addRow("시작 값 번호", self.start_field_spin)
        form.addRow("끝 값 번호", self.end_field_spin)
        form.addRow("찾을 단어", self.keyword_edit)
        form.addRow("정규식", self.regex_edit)
        form.addRow("Python 추출 함수", self.python_parser_combo)
        right.addLayout(form)
        right.addWidget(self.preview_label)
        self.sample_line_view = QPlainTextEdit()
        self.sample_line_view.setReadOnly(True)
        right.addWidget(self.sample_line_view, 1)

        layout.addLayout(left, 2)
        layout.addLayout(right, 4)
        self.tabs.addTab(tab, "Excel 컬럼")

        for widget in (self.column_name_edit, self.keyword_edit, self.regex_edit):
            widget.textChanged.connect(self._on_column_changed)
        self.column_command_combo.currentIndexChanged.connect(self._on_column_changed)
        self.extract_method_combo.currentIndexChanged.connect(self._on_column_changed)
        self.line_number_spin.valueChanged.connect(self._on_column_changed)
        self.start_field_spin.valueChanged.connect(self._on_column_changed)
        self.end_field_spin.valueChanged.connect(self._on_column_changed)
        self.python_parser_combo.currentTextChanged.connect(self._on_column_changed)

    def _build_review_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.issue_list = QListWidget()
        layout.addWidget(self.issue_list, 1)
        self.summary_preview = QPlainTextEdit()
        self.summary_preview.setReadOnly(True)
        layout.addWidget(self.summary_preview, 2)
        self.tabs.addTab(tab, "미리보기/저장")

    def _build_advanced_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel("전문가 설정입니다. 정규식, Python 추출 함수, YAML 원문, 접속 문제 해결 설정이 필요할 때만 사용하세요.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        create_parser_button = make_action_button("Python 함수", ActionKind.ADD, tooltip="Python 추출 함수를 만듭니다.")
        create_parser_button.clicked.connect(self._open_python_parser_dialog)
        open_parser_button = make_action_button("함수 폴더", ActionKind.OPEN, tooltip="Python 추출 함수 폴더를 엽니다.")
        open_parser_button.clicked.connect(lambda: os.startfile(str(self.service.custom_parsers_dir)))
        export_button = make_action_button("YAML 저장", ActionKind.EXPORT)
        export_button.clicked.connect(self._export_yaml)
        generate_button = make_action_button("기본 파일", ActionKind.ADD, tooltip="기본 템플릿 파일을 생성합니다.")
        generate_button.clicked.connect(self._generate_all_templates)
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
        self.yaml_preview = QPlainTextEdit()
        layout.addWidget(self.yaml_preview, 1)
        self.tabs.addTab(tab, "전문가 설정")
        self.ssh_device_type_edit.textChanged.connect(self._schedule_preview)
        self.telnet_device_type_edit.textChanged.connect(self._schedule_preview)

    def _load_template_choices(self) -> None:
        self.copy_template_combo.blockSignals(True)
        self.copy_template_combo.clear()
        self.copy_template_combo.addItem("새 템플릿", None)
        for template in self.templates:
            label = template.get("display_name") or f"{template['vendor']} / {template['os']}"
            if template.get("is_reference") and "참고용" not in label:
                label = f"{label} (참고용)"
            self.copy_template_combo.addItem(label, template)
        self.copy_template_combo.blockSignals(False)
        self._refresh_python_parser_choices()

    def _refresh_python_parser_choices(self, selected: str = "") -> None:
        current = selected or self.python_parser_combo.currentText() if hasattr(self, "python_parser_combo") else ""
        self.python_parser_combo.blockSignals(True)
        self.python_parser_combo.clear()
        self.python_parser_combo.addItem("")
        for name in self.service.available_custom_parsers():
            self.python_parser_combo.addItem(name)
        if current:
            self.python_parser_combo.setCurrentText(current)
        self.python_parser_combo.blockSignals(False)

    def _load_state(self) -> None:
        self.vendor_edit.setText(self.state["vendor"])
        self.model_edit.setText(self.state["model"])
        self.os_edit.setText(self.state["os"])
        self.os_version_edit.setText(self.state["os_version"])
        self.connection_combo.setCurrentIndex(0)
        self.backup_enabled_check.setChecked(bool(self.state["backup_enabled"]))
        self.backup_command_edit.setText(self.state["backup_command"])
        self.ssh_device_type_edit.setText(self.state["ssh_device_type"])
        self.telnet_device_type_edit.setText(self.state["telnet_device_type"])
        self._refresh_command_list(0)
        self._refresh_column_commands()
        self._refresh_column_list(0)

    def _collect_state(self) -> None:
        self._persist_command()
        self._persist_column()
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
            item.setText(self.command_edit.text().strip() or f"명령 {self._selected_command_row + 1}")
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
            self.extract_method_combo.setCurrentIndex(max(0, self.extract_method_combo.findData(data.get("method", "split_fields"))))
            self.line_number_spin.setValue(int(data.get("line_number", 1) or 1))
            self.start_field_spin.setValue(int(data.get("start_field", 1) or 1))
            self.end_field_spin.setValue(int(data.get("end_field", data.get("start_field", 1)) or 1))
            self.keyword_edit.setText(data.get("keyword", ""))
            self.regex_edit.setText(data.get("regex", ""))
            self.python_parser_combo.setCurrentText(data.get("python_parser", ""))
        finally:
            self._loading_column = False
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
        self._load_column(row)
        self._schedule_preview()

    def _on_column_changed(self) -> None:
        if self._loading_column:
            return
        self._persist_column()
        item = self.column_list.item(self._selected_column_row)
        if item:
            item.setText(self.column_name_edit.text().strip() or f"컬럼 {self._selected_column_row + 1}")
        self._refresh_extraction_preview()
        self._schedule_preview()

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
        return [row.get("command", "").strip() for row in self.state["commands"] if row.get("command", "").strip()]

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
            fields = " | ".join(f"{i}:{value}" for i, value in enumerate(line.split(), start=1))
            numbered.append(f"{marker} {idx:02d}: {line}\n     {fields}")
        self.sample_line_view.setPlainText("\n".join(numbered))
        result = self._preview_value(sample)
        self.preview_label.setText(f"추출 결과: {result or '-'}")

    def _preview_value(self, sample: str) -> str:
        method = self.extract_method_combo.currentData()
        lines = sample.splitlines()
        line_number = self.line_number_spin.value()
        if method == "split_fields":
            if not (1 <= line_number <= len(lines)):
                return ""
            fields = lines[line_number - 1].split()
            start = self.start_field_spin.value()
            end = self.end_field_spin.value()
            if not (1 <= start <= len(fields)):
                return ""
            return " ".join(fields[start - 1:min(max(end, start), len(fields))])
        if method == "keyword_after":
            keyword = self.keyword_edit.text().strip()
            if not keyword:
                return ""
            for line in lines:
                if keyword in line:
                    return line.split(keyword, 1)[1].strip(" :\t")
        if method == "line_text":
            if 1 <= line_number <= len(lines):
                return lines[line_number - 1].strip()
        return ""

    def _copy_selected_template(self) -> None:
        template = self.copy_template_combo.currentData()
        if not isinstance(template, dict):
            return
        self.state["vendor"] = template.get("vendor", "")
        self.state["os"] = template.get("os", "")
        self.state["commands"] = [{"command": command, "sample": ""} for command in template.get("commands", [])] or self.state["commands"]
        self.state["backup_command"] = template.get("backup_command", "")
        self.state["backup_enabled"] = bool(template.get("backup_command"))
        self.state["columns"] = self._columns_from_template(template)
        connection = template.get("connection_overrides") or {}
        self.state["ssh_device_type"] = connection.get("ssh") or connection.get("default") or ""
        self.state["telnet_device_type"] = connection.get("telnet") or ""
        self._load_state()
        self._schedule_preview()

    def _columns_from_template(self, template: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for command, rule in (template.get("parsing_rules") or {}).items():
            if not isinstance(rule, dict):
                continue
            rule_list = rule.get("patterns") if isinstance(rule.get("patterns"), list) else [rule]
            for item in rule_list:
                if not isinstance(item, dict):
                    continue
                if item.get("parser_type") in {"split_fields", "keyword_after", "line_text"}:
                    rows.append(
                        {
                            "name": item.get("output_column", ""),
                            "command": command,
                            "method": item.get("parser_type", "split_fields"),
                            "line_number": int(item.get("line_number", 1) or 1),
                            "start_field": int(item.get("start_field", 1) or 1),
                            "end_field": int(item.get("end_field", item.get("start_field", 1)) or 1),
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
        issues = self._validation_issues()
        parser_rows = self._parser_rows_from_columns()
        try:
            self.latest_yaml_text = self.service.build_simple_custom_rules_yaml(
                vendor=self.state["vendor"],
                os_name=self.state["os"],
                inspection_commands=self._commands(),
                backup_command=self.state["backup_command"] if self.state["backup_enabled"] else "",
                default_device_type=self.state["ssh_device_type"],
                telnet_device_type=self.state["telnet_device_type"],
                parser_rows=parser_rows,
                model=self.state["model"],
                os_version=self.state["os_version"],
                output_columns=[row.get("name", "") for row in self.state["columns"]],
            )
        except Exception as exc:
            issues.append(str(exc))
            self.latest_yaml_text = "# 입력을 완성하면 템플릿 YAML이 생성됩니다.\n"

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
                    "parser_type": method if method in {"split_fields", "keyword_after", "line_text"} else "",
                    "line_number": column.get("line_number", 1),
                    "start_field": column.get("start_field", 1),
                    "end_field": column.get("end_field", column.get("start_field", 1)),
                    "keyword": column.get("keyword", ""),
                    "pattern": column.get("regex", "") if method == "regex" else "",
                    "custom_parser": column.get("python_parser", "") if method == "python" else "",
                }
            )
        return rows

    def _validation_issues(self) -> list[str]:
        issues: list[str] = []
        if not self.state["vendor"]:
            issues.append("벤더를 입력하세요. 예: Cisco")
        if not self.state["os"]:
            issues.append("OS를 입력하세요. 예: IOS-XE")
        if not self._commands():
            issues.append("점검 명령을 하나 이상 입력하세요. 예: show version")
        for column in self.state["columns"]:
            if not column.get("name"):
                issues.append("Excel 컬럼명을 입력하세요. 예: OS버전")
            if not column.get("command"):
                issues.append(f"{column.get('name') or '컬럼'}: 가져올 명령 결과를 선택하세요.")
            if column.get("method") == "keyword_after" and not column.get("keyword"):
                issues.append(f"{column.get('name')}: 찾을 단어를 입력하세요. 예: Processor board ID")
            if column.get("method") == "regex" and not column.get("regex"):
                issues.append(f"{column.get('name')}: 정규식을 입력하세요.")
            if column.get("method") == "python" and not column.get("python_parser"):
                issues.append(f"{column.get('name')}: Python 추출 함수를 선택하거나 만드세요.")
        return issues

    def _human_summary(self, parser_rows: list[dict[str, Any]], issues: list[str]) -> str:
        lines = [
            f"장비: {self.state['vendor']} {self.state['model']} / {self.state['os']} {self.state['os_version']}".strip(),
            f"접속: {self.connection_combo.currentText()}",
            "",
            "[실행할 명령]",
            *[f"- {command}" for command in self._commands()],
            "",
            "[Excel 컬럼]",
        ]
        for row in parser_rows:
            method = row.get("parser_type") or ("정규식" if row.get("pattern") else "Python")
            lines.append(f"- {row.get('output_column')} <- {row.get('command')} ({method})")
        if issues:
            lines.extend(["", "[저장 전 확인]", *[f"- {issue}" for issue in issues]])
        return "\n".join(lines)

    def _save_template(self) -> None:
        self.refresh_preview()
        if not self.save_button.isEnabled():
            self.tabs.setCurrentIndex(3)
            return
        try:
            path = self.service.save_custom_rules_text(self.latest_yaml_text)
        except Exception as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))
            return
        QMessageBox.information(self, "템플릿 저장 완료", f"템플릿을 저장했습니다.\n{path}")

    def _open_python_parser_dialog(self) -> None:
        self._parser_dialog = PythonParserDialog(self.service, self)
        if self._parser_dialog.exec() and self._parser_dialog.saved_function_name:
            function_name = self._parser_dialog.saved_function_name
            self._refresh_python_parser_choices(function_name)
            self.extract_method_combo.setCurrentIndex(self.extract_method_combo.findData("python"))
            self.python_parser_combo.setCurrentText(function_name)
            self._on_column_changed()

    def _export_yaml(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "YAML 내보내기", "custom_rules.yaml", "YAML Files (*.yaml *.yml)")
        if not path:
            return
        Path(path).write_text(self.yaml_preview.toPlainText(), encoding="utf-8")
        QMessageBox.information(self, "내보내기 완료", path)

    def _generate_all_templates(self) -> None:
        try:
            count = self.service.ensure_vendor_template_files()
        except Exception as exc:
            QMessageBox.warning(self, "템플릿 생성 실패", str(exc))
            return
        QMessageBox.information(self, "템플릿 생성 완료", f"{count}개 기본 템플릿 파일을 생성했습니다.\n{self.service.vendor_templates_dir}")
        os.startfile(str(self.service.vendor_templates_dir))

    def _schedule_preview(self) -> None:
        self.preview_timer.start()
