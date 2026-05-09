from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .authoring import (
    build_profile_yaml_from_state,
    make_empty_block_row,
    make_empty_profile_builder_state,
    make_empty_variable_row,
    profile_to_builder_state,
    save_profile_yaml_to_directory,
)
from .app_icon import build_app_icon
from .models import (
    AUTO_INCREMENT_IPV4,
    AUTO_INCREMENT_NONE,
    AUTO_INCREMENT_SUFFIX_NUMBER,
    Profile,
)


from netops_suite.ui.actions import ActionKind, make_action_button

ERROR_BG = QColor("#fff1ed")
OK_BG = QColor("#eef8eb")
AUTO_INCREMENT_ITEMS = (
    ("증가 안 함", AUTO_INCREMENT_NONE),
    ("끝 숫자 증가", AUTO_INCREMENT_SUFFIX_NUMBER),
    ("IPv4 증가", AUTO_INCREMENT_IPV4),
)


class ProfileBuilderDialog(QDialog):
    def __init__(
        self,
        profiles_dir: str | Path,
        profile: Profile | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        app = QApplication.instance()
        if app and not app.windowIcon().isNull():
            self.setWindowIcon(app.windowIcon())
        else:
            self.setWindowIcon(build_app_icon())
        self.profiles_dir = Path(profiles_dir)
        self.state = profile_to_builder_state(profile) if profile else make_empty_profile_builder_state()
        self.saved_profile_id = ""
        self.saved_path: Path | None = None
        self.latest_yaml_text = ""
        self.latest_issues: list[str] = []
        self._loading_variable_editor = False
        self._loading_block_editor = False
        self._selected_variable_row = -1
        self._selected_block_row = -1

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(180)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.refresh_preview)

        self.setWindowTitle("프로파일 편집" if profile else "프로파일 작성")
        self.resize(1080, 820)
        self._build_ui()
        self._load_state_into_widgets()
        self.refresh_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "프로파일 ID는 장비 설정 정보 파일의 profile_id 컬럼 값과 연결됩니다. "
            "장비 파일에는 프로파일 ID(profile_id) 컬럼이 필수이고, device_id 컬럼은 선택입니다. 아래 변수명이 장비 파일 컬럼으로 들어갑니다."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_basic_tab()
        self._build_variables_tab()
        self._build_blocks_tab()
        self._build_review_tab()

        actions = QHBoxLayout()
        actions.addStretch(1)
        refresh_button = make_action_button("검토 갱신", ActionKind.REFRESH)
        refresh_button.clicked.connect(self.refresh_preview)
        self.save_button = make_action_button("프로파일 저장", ActionKind.SAVE)
        self.save_button.clicked.connect(self.save_profile)
        close_button = make_action_button("닫기", ActionKind.CANCEL)
        close_button.clicked.connect(self.reject)
        actions.addWidget(refresh_button)
        actions.addWidget(self.save_button)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def _build_basic_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel("프로파일 ID는 공백 없이 작성하고, 장비 파일의 profile_id 컬럼 값과 정확히 같아야 합니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        example = QLabel("예: CISCO_IOS_L2_ACCESS_BASE / C9300_BRANCH_DISTRIBUTION")
        example.setWordWrap(True)
        layout.addWidget(example)

        form = QFormLayout()
        self.profile_id_edit = QLineEdit()
        self.profile_id_edit.setPlaceholderText("예: CISCO_IOS_L2_ACCESS_BASE")
        self.vendor_edit = QLineEdit()
        self.vendor_edit.setPlaceholderText("예: CISCO")
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("예: Catalyst 9300")
        self.firmware_edit = QLineEdit()
        self.firmware_edit.setPlaceholderText("예: IOS-XE 17.x")
        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedHeight(110)
        self.description_edit.setPlaceholderText("이 프로파일이 어떤 장비/용도에 쓰이는지 설명")
        form.addRow("프로파일 ID", self.profile_id_edit)
        form.addRow("벤더", self.vendor_edit)
        form.addRow("모델", self.model_edit)
        form.addRow("펌웨어", self.firmware_edit)
        form.addRow("설명", self.description_edit)
        layout.addLayout(form)
        layout.addStretch(1)
        self.tabs.addTab(tab, "기본 정보")

        self.profile_id_edit.textChanged.connect(self._schedule_preview)
        self.vendor_edit.textChanged.connect(self._schedule_preview)
        self.model_edit.textChanged.connect(self._schedule_preview)
        self.firmware_edit.textChanged.connect(self._schedule_preview)
        self.description_edit.textChanged.connect(self._schedule_preview)

    def _build_variables_tab(self) -> None:
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left = QVBoxLayout()
        left_hint = QLabel("변수명은 장비 파일 컬럼명이 됩니다. 기본값이 있으면 장비 파일에서 비워도 자동 적용됩니다.")
        left_hint.setWordWrap(True)
        left.addWidget(left_hint)
        left_example = QLabel("예: hostname, mgmt_ip, enable_ntp, local_admin_secret")
        left_example.setWordWrap(True)
        left.addWidget(left_example)
        increment_hint = QLabel("연속 값 복사 시 자동으로 바뀔 변수는 오른쪽 '연속 값 규칙'에서 지정합니다.")
        increment_hint.setWordWrap(True)
        left.addWidget(increment_hint)
        self.variable_list = QListWidget()
        self.variable_list.currentRowChanged.connect(self._on_variable_selection_changed)
        left.addWidget(self.variable_list, 1)
        left_buttons = QHBoxLayout()
        add_button = make_action_button("변수 추가", ActionKind.ADD)
        add_button.clicked.connect(self.add_variable)
        remove_button = make_action_button("변수 삭제", ActionKind.DELETE)
        remove_button.clicked.connect(self.remove_variable)
        left_buttons.addWidget(add_button)
        left_buttons.addWidget(remove_button)
        left.addLayout(left_buttons)

        right = QVBoxLayout()
        form = QFormLayout()
        self.variable_name_edit = QLineEdit()
        self.variable_name_edit.setPlaceholderText("예: hostname")
        self.variable_type_combo = QComboBox()
        self.variable_type_combo.addItems(["string", "ipv4", "bool", "int"])
        self.variable_required_check = QCheckBox("필수값")
        self.variable_default_edit = QLineEdit()
        self.variable_default_edit.setPlaceholderText("예: 255.255.255.0 / true / 99")
        self.variable_auto_increment_combo = QComboBox()
        for label, value in AUTO_INCREMENT_ITEMS:
            self.variable_auto_increment_combo.addItem(label, value)
        self.variable_description_edit = QLineEdit()
        self.variable_description_edit.setPlaceholderText("예: 관리 VLAN 번호")
        form.addRow("변수명", self.variable_name_edit)
        form.addRow("타입", self.variable_type_combo)
        form.addRow("필수 여부", self.variable_required_check)
        form.addRow("기본값", self.variable_default_edit)
        form.addRow("연속 값 규칙", self.variable_auto_increment_combo)
        form.addRow("설명", self.variable_description_edit)
        right.addLayout(form)
        right.addStretch(1)

        layout.addLayout(left, 2)
        layout.addLayout(right, 3)
        self.tabs.addTab(tab, "변수")

        self.variable_name_edit.textChanged.connect(self._on_variable_editor_changed)
        self.variable_type_combo.currentTextChanged.connect(self._on_variable_editor_changed)
        self.variable_required_check.toggled.connect(self._on_variable_editor_changed)
        self.variable_default_edit.textChanged.connect(self._on_variable_editor_changed)
        self.variable_auto_increment_combo.currentIndexChanged.connect(self._on_variable_editor_changed)
        self.variable_description_edit.textChanged.connect(self._on_variable_editor_changed)

    def _build_blocks_tab(self) -> None:
        tab = QWidget()
        layout = QHBoxLayout(tab)

        left = QVBoxLayout()
        left_hint = QLabel("명령 블록은 위에서 아래 순서로 출력됩니다. 블록을 빼고 싶으면 메인 화면의 블록 선택 패널에서 끌 수 있습니다.")
        left_hint.setWordWrap(True)
        left.addWidget(left_hint)
        left_example = QLabel("예: 명령어 = hostname {{ hostname }}")
        left_example.setWordWrap(True)
        left.addWidget(left_example)
        self.block_list = QListWidget()
        self.block_list.currentRowChanged.connect(self._on_block_selection_changed)
        left.addWidget(self.block_list, 1)
        left_buttons = QHBoxLayout()
        add_button = make_action_button("블록 추가", ActionKind.ADD)
        add_button.clicked.connect(self.add_block)
        remove_button = make_action_button("블록 삭제", ActionKind.DELETE)
        remove_button.clicked.connect(self.remove_block)
        left_buttons.addWidget(add_button)
        left_buttons.addWidget(remove_button)
        left.addLayout(left_buttons)

        right = QVBoxLayout()
        form = QFormLayout()
        self.block_name_edit = QLineEdit()
        self.block_name_edit.setPlaceholderText("예: base")
        self.block_lines_edit = QPlainTextEdit()
        self.block_lines_edit.setMinimumHeight(280)
        self.block_lines_edit.setPlaceholderText("hostname {{ hostname }}\ninterface vlan {{ mgmt_vlan }}\n ip address {{ mgmt_ip }} {{ mgmt_mask }}")
        form.addRow("블록 이름", self.block_name_edit)
        form.addRow("명령어", self.block_lines_edit)
        right.addLayout(form)
        right.addStretch(1)

        layout.addLayout(left, 2)
        layout.addLayout(right, 3)
        self.tabs.addTab(tab, "명령 블록")

        self.block_name_edit.textChanged.connect(self._on_block_editor_changed)
        self.block_lines_edit.textChanged.connect(self._on_block_editor_changed)

    def _build_review_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel("왼쪽 탭에서 입력한 내용으로 YAML이 자동 생성됩니다. 오류가 없으면 바로 프로파일 폴더에 저장됩니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.issue_list = QListWidget()
        layout.addWidget(self.issue_list, 1)
        self.yaml_preview = QPlainTextEdit()
        self.yaml_preview.setReadOnly(True)
        layout.addWidget(self.yaml_preview, 2)
        self.tabs.addTab(tab, "검토/저장")

    def _schedule_preview(self) -> None:
        self.preview_timer.start()

    def _load_state_into_widgets(self) -> None:
        self.profile_id_edit.setText(str(self.state.get("id", "")))
        self.vendor_edit.setText(str(self.state.get("vendor", "")))
        self.model_edit.setText(str(self.state.get("model", "")))
        self.firmware_edit.setText(str(self.state.get("firmware", "")))
        self.description_edit.setPlainText(str(self.state.get("description", "")))
        self._refresh_variable_list(0)
        self._refresh_block_list(0)

    def _refresh_variable_list(self, index: int | None = None) -> None:
        current = self._selected_variable_row if index is None else index
        self.variable_list.blockSignals(True)
        self.variable_list.clear()
        for row_index, row in enumerate(self.state["variables"], start=1):
            name = str(row.get("name", "")).strip() or f"변수 {row_index}"
            if row.get("required"):
                name = f"{name} *"
            self.variable_list.addItem(QListWidgetItem(name))
        target = min(max(current or 0, 0), self.variable_list.count() - 1)
        if self.variable_list.count():
            self.variable_list.setCurrentRow(target)
        self.variable_list.blockSignals(False)
        self._selected_variable_row = target if self.variable_list.count() else -1
        self._load_variable_editor(self._selected_variable_row)

    def _refresh_block_list(self, index: int | None = None) -> None:
        current = self._selected_block_row if index is None else index
        self.block_list.blockSignals(True)
        self.block_list.clear()
        for row_index, row in enumerate(self.state["blocks"], start=1):
            name = str(row.get("name", "")).strip() or f"블록 {row_index}"
            self.block_list.addItem(QListWidgetItem(name))
        target = min(max(current or 0, 0), self.block_list.count() - 1)
        if self.block_list.count():
            self.block_list.setCurrentRow(target)
        self.block_list.blockSignals(False)
        self._selected_block_row = target if self.block_list.count() else -1
        self._load_block_editor(self._selected_block_row)

    def _persist_variable_editor(self, row: int | None = None) -> None:
        target_row = self._selected_variable_row if row is None else row
        if target_row < 0 or self._loading_variable_editor:
            return
        self.state["variables"][target_row] = {
            **self.state["variables"][target_row],
            "name": self.variable_name_edit.text().strip(),
            "type": self.variable_type_combo.currentText().strip(),
            "required": self.variable_required_check.isChecked(),
            "default_input": self.variable_default_edit.text().strip(),
            "auto_increment": str(self.variable_auto_increment_combo.currentData() or AUTO_INCREMENT_NONE).strip(),
            "description": self.variable_description_edit.text().strip(),
        }

    def _persist_block_editor(self, row: int | None = None) -> None:
        target_row = self._selected_block_row if row is None else row
        if target_row < 0 or self._loading_block_editor:
            return
        self.state["blocks"][target_row] = {
            **self.state["blocks"][target_row],
            "name": self.block_name_edit.text().strip(),
            "lines_text": self.block_lines_edit.toPlainText().rstrip(),
        }

    def _load_variable_editor(self, row: int) -> None:
        self._loading_variable_editor = True
        try:
            if row < 0 or row >= len(self.state["variables"]):
                self.variable_name_edit.clear()
                self.variable_type_combo.setCurrentText("string")
                self.variable_required_check.setChecked(False)
                self.variable_default_edit.clear()
                self.variable_auto_increment_combo.setCurrentIndex(0)
                self.variable_description_edit.clear()
                return
            item = self.state["variables"][row]
            self.variable_name_edit.setText(str(item.get("name", "")))
            self.variable_type_combo.setCurrentText(str(item.get("type", "string")) or "string")
            self.variable_required_check.setChecked(bool(item.get("required", False)))
            self.variable_default_edit.setText(str(item.get("default_input", "")))
            auto_increment = str(item.get("auto_increment", AUTO_INCREMENT_NONE)).strip() or AUTO_INCREMENT_NONE
            selected_index = next(
                (index for index in range(self.variable_auto_increment_combo.count()) if self.variable_auto_increment_combo.itemData(index) == auto_increment),
                0,
            )
            self.variable_auto_increment_combo.setCurrentIndex(selected_index)
            self.variable_description_edit.setText(str(item.get("description", "")))
        finally:
            self._loading_variable_editor = False

    def _load_block_editor(self, row: int) -> None:
        self._loading_block_editor = True
        try:
            if row < 0 or row >= len(self.state["blocks"]):
                self.block_name_edit.clear()
                self.block_lines_edit.clear()
                return
            item = self.state["blocks"][row]
            self.block_name_edit.setText(str(item.get("name", "")))
            self.block_lines_edit.setPlainText(str(item.get("lines_text", "")))
        finally:
            self._loading_block_editor = False

    def _update_current_variable_item_text(self) -> None:
        row = self._selected_variable_row
        if row < 0:
            return
        item = self.variable_list.item(row)
        if item is None:
            return
        row_data = self.state["variables"][row]
        name = str(row_data.get("name", "")).strip() or f"변수 {row + 1}"
        if row_data.get("required"):
            name = f"{name} *"
        auto_increment = str(row_data.get("auto_increment", AUTO_INCREMENT_NONE)).strip()
        if auto_increment == AUTO_INCREMENT_SUFFIX_NUMBER:
            name = f"{name} [번호+]"
        elif auto_increment == AUTO_INCREMENT_IPV4:
            name = f"{name} [IP+]"
        item.setText(name)

    def _update_current_block_item_text(self) -> None:
        row = self._selected_block_row
        if row < 0:
            return
        item = self.block_list.item(row)
        if item is None:
            return
        row_data = self.state["blocks"][row]
        name = str(row_data.get("name", "")).strip() or f"블록 {row + 1}"
        item.setText(name)

    def _on_variable_selection_changed(self, row: int) -> None:
        previous_row = self._selected_variable_row
        if previous_row != row:
            self._persist_variable_editor(previous_row)
        self._selected_variable_row = row
        self._load_variable_editor(row)
        self._schedule_preview()

    def _on_block_selection_changed(self, row: int) -> None:
        previous_row = self._selected_block_row
        if previous_row != row:
            self._persist_block_editor(previous_row)
        self._selected_block_row = row
        self._load_block_editor(row)
        self._schedule_preview()

    def _on_variable_editor_changed(self) -> None:
        if self._loading_variable_editor:
            return
        self._persist_variable_editor()
        self._update_current_variable_item_text()
        self._schedule_preview()

    def _on_block_editor_changed(self) -> None:
        if self._loading_block_editor:
            return
        self._persist_block_editor()
        self._update_current_block_item_text()
        self._schedule_preview()

    def add_variable(self) -> None:
        self._persist_variable_editor()
        self.state["variables"].append(make_empty_variable_row())
        self._refresh_variable_list(len(self.state["variables"]) - 1)
        self._schedule_preview()

    def remove_variable(self) -> None:
        row = self.variable_list.currentRow()
        if row < 0:
            return
        if len(self.state["variables"]) == 1:
            self.state["variables"] = [make_empty_variable_row()]
            self._refresh_variable_list(0)
        else:
            self.state["variables"].pop(row)
            self._refresh_variable_list(max(0, row - 1))
        self._schedule_preview()

    def add_block(self) -> None:
        self._persist_block_editor()
        self.state["blocks"].append(make_empty_block_row())
        self._refresh_block_list(len(self.state["blocks"]) - 1)
        self._schedule_preview()

    def remove_block(self) -> None:
        row = self.block_list.currentRow()
        if row < 0:
            return
        if len(self.state["blocks"]) == 1:
            self.state["blocks"] = [make_empty_block_row()]
            self._refresh_block_list(0)
        else:
            self.state["blocks"].pop(row)
            self._refresh_block_list(max(0, row - 1))
        self._schedule_preview()

    def _collect_state(self) -> dict[str, Any]:
        self._persist_variable_editor()
        self._persist_block_editor()
        self.state["id"] = self.profile_id_edit.text().strip()
        self.state["vendor"] = self.vendor_edit.text().strip()
        self.state["model"] = self.model_edit.text().strip()
        self.state["firmware"] = self.firmware_edit.text().strip()
        self.state["description"] = self.description_edit.toPlainText().strip()
        return {
            "id": self.state.get("id", ""),
            "vendor": self.state.get("vendor", ""),
            "model": self.state.get("model", ""),
            "firmware": self.state.get("firmware", ""),
            "description": self.state.get("description", ""),
            "variables": [dict(row) for row in self.state["variables"]],
            "blocks": [dict(row) for row in self.state["blocks"]],
        }

    def refresh_preview(self) -> None:
        state = self._collect_state()
        yaml_text, issues = build_profile_yaml_from_state(state)
        self.latest_yaml_text = yaml_text
        self.latest_issues = list(issues)
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
        self.yaml_preview.setPlainText(yaml_text)
        self.save_button.setEnabled(not issues and bool(str(state.get("id", "")).strip()))

    def save_profile(self) -> None:
        self.refresh_preview()
        if self.latest_issues:
            QMessageBox.warning(self, "검토 필요", "오류를 먼저 해결한 뒤 저장하세요.")
            self.tabs.setCurrentIndex(3)
            return
        profile_id = str(self._collect_state().get("id", "")).strip()
        target_path, _ = save_profile_yaml_to_directory(profile_id, self.latest_yaml_text, self.profiles_dir)
        self.saved_profile_id = profile_id
        self.saved_path = target_path
        QMessageBox.information(self, "프로파일 저장 완료", f"{target_path.name} 파일로 저장했습니다.")
        self.accept()
