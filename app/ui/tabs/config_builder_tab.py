from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from netops_suite.modules.config_builder import ConfigBuilderRenderResult, ConfigBuilderService
from netops_suite.modules.config_builder.switch_configurator.models import Profile


from netops_suite.ui.actions import ActionKind, make_action_button

class ConfigBuilderTab(QWidget):
    def __init__(self, state: AppState | None = None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        user_data = state.paths.data_root / "config_builder" if state else None
        self.service = ConfigBuilderService(user_data_dir=user_data)
        self._profiles: dict[str, Profile] = {}
        self._last_result: ConfigBuilderRenderResult | None = None
        self._builder_window = None
        self._device_values_path = ""
        self._current_render_index = 0
        self._build_ui()
        self._refresh_profiles()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        input_action_row = QHBoxLayout()
        self.open_device_values_button = make_action_button("장비값 열기", ActionKind.BROWSE)
        self.open_device_values_button.setObjectName("configBuilderOpenDeviceValuesButton")
        self.open_device_values_button.clicked.connect(self._pick_device_values)
        self.render_button = make_action_button("CLI 생성", ActionKind.PRIMARY)
        self.render_button.setObjectName("configBuilderRenderButton")
        self.render_button.clicked.connect(self._render)
        input_action_row.addWidget(self.open_device_values_button)
        input_action_row.addWidget(self.render_button)
        input_action_row.addStretch(1)
        layout.addLayout(input_action_row)

        self.device_values_path_label = QLabel("장비값 CSV/XLSX를 선택하세요.")
        self.device_values_path_label.setWordWrap(True)
        layout.addWidget(self.device_values_path_label)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        profile_action_row = QHBoxLayout()
        profile_action_row.addWidget(QLabel("프로파일"))
        profile_action_row.addStretch(1)
        self.edit_profile_button = make_action_button("선택 편집", ActionKind.EDIT)
        self.edit_profile_button.setObjectName("configBuilderEditProfileButton")
        self.edit_profile_button.clicked.connect(self._open_selected_profile_editor)
        self.full_editor_button = make_action_button("고급 편집기", ActionKind.EDIT)
        self.full_editor_button.setObjectName("configBuilderFullEditorButton")
        self.full_editor_button.clicked.connect(self._open_full_editor)
        profile_action_row.addWidget(self.edit_profile_button)
        profile_action_row.addWidget(self.full_editor_button)
        left_layout.addLayout(profile_action_row)
        self.profile_table = QTableWidget(0, 5)
        self.profile_table.setHorizontalHeaderLabels(["프로파일 ID", "벤더", "모델", "펌웨어", "블록"])
        self.profile_table.horizontalHeader().setStretchLastSection(True)
        self.profile_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.profile_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.profile_table.itemSelectionChanged.connect(self._on_profile_selection_changed)
        left_layout.addWidget(self.profile_table, 2)
        left_layout.addWidget(QLabel("선택 프로파일 상세"))
        self.profile_detail_view = QPlainTextEdit()
        self.profile_detail_view.setReadOnly(True)
        self.profile_detail_view.setMaximumHeight(170)
        left_layout.addWidget(self.profile_detail_view, 1)
        left_layout.addWidget(QLabel("이번 생성에서 제외할 블록"))
        self.block_list = QListWidget()
        left_layout.addWidget(self.block_list, 1)
        left_layout.addWidget(QLabel("검증/작업 로그"))
        self.issue_view = QPlainTextEdit()
        self.issue_view.setReadOnly(True)
        left_layout.addWidget(self.issue_view, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        result_action_row = QHBoxLayout()
        result_action_row.addWidget(QLabel("생성 결과"))
        result_action_row.addStretch(1)
        self.copy_button = make_action_button("선택 CLI 복사", ActionKind.COPY)
        self.copy_button.setObjectName("configBuilderCopyButton")
        self.copy_button.clicked.connect(self._copy_selected)
        self.copy_next_button = make_action_button("복사 후 다음", ActionKind.COPY)
        self.copy_next_button.setObjectName("configBuilderCopyNextButton")
        self.copy_next_button.clicked.connect(self._copy_and_next)
        self.save_button = make_action_button("전체 TXT 저장", ActionKind.SAVE)
        self.save_button.setObjectName("configBuilderSaveBundleButton")
        self.save_button.clicked.connect(self._save_bundle)
        self.save_each_button = make_action_button("장비별 TXT 저장", ActionKind.SAVE)
        self.save_each_button.setObjectName("configBuilderSaveEachButton")
        self.save_each_button.clicked.connect(self._save_each)
        for button in (self.copy_button, self.copy_next_button, self.save_button, self.save_each_button):
            result_action_row.addWidget(button)
        right_layout.addLayout(result_action_row)
        self.result_table = QTableWidget(0, 4)
        self.result_table.setHorizontalHeaderLabels(["장비", "프로파일", "상태", "길이"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.itemSelectionChanged.connect(self._select_result_from_table)
        right_layout.addWidget(self.result_table, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right_layout.addWidget(self.preview, 3)
        splitter.addWidget(right)
        splitter.setSizes([420, 700])
        layout.addWidget(splitter, 1)
        self._update_action_states()

    def _refresh_profiles(self, select_profile_id: str | None = None) -> None:
        previous_profile_id = select_profile_id or self._selected_profile_id()
        profiles, issues = self.service.load_profiles()
        self._profiles = profiles
        self.profile_table.blockSignals(True)
        self.profile_table.setRowCount(len(profiles))
        selected_row = -1
        for row, profile in enumerate(profiles.values()):
            values = [
                profile.id,
                profile.vendor,
                profile.model,
                profile.firmware,
                ", ".join(block.name for block in profile.blocks),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, {"profile_id": profile.id, "source": profile.source})
                self.profile_table.setItem(row, column, item)
            if profile.id == previous_profile_id:
                selected_row = row
        if selected_row < 0 and profiles:
            selected_row = 0
        if selected_row >= 0:
            self.profile_table.selectRow(selected_row)
        self.profile_table.blockSignals(False)
        self.profile_table.resizeColumnsToContents()
        if issues:
            self.issue_view.setPlainText("\n".join(self._format_issue(issue) for issue in issues))
        else:
            self.issue_view.setPlainText(f"프로파일 {len(profiles)}개를 불러왔습니다. 프로파일 폴더: {self.service.profiles_dir}")
        self._on_profile_selection_changed()

    def _on_profile_selection_changed(self) -> None:
        self._refresh_block_list()
        self._refresh_profile_detail()
        self._update_action_states()

    def _refresh_block_list(self) -> None:
        self.block_list.clear()
        blocks: list[str] = []
        for profile_id in self._selected_profile_ids():
            profile = self._profiles.get(profile_id)
            if profile:
                block_names = [block.name for block in profile.blocks]
            else:
                row = self._row_for_profile_id(profile_id)
                item = self.profile_table.item(row, 4) if row is not None else None
                block_names = [block.strip() for block in item.text().split(",")] if item else []
            for block_name in block_names:
                if block_name and block_name not in blocks:
                    blocks.append(block_name)
        for block in blocks:
            item = QListWidgetItem(block)
            item.setCheckState(Qt.Unchecked)
            self.block_list.addItem(item)

    def _refresh_profile_detail(self) -> None:
        profile = self._selected_profile()
        if not profile:
            self.profile_detail_view.setPlainText("프로파일을 선택하면 상세 정보가 표시됩니다.")
            return

        description = profile.description_ko or profile.description or "(설명 없음)"
        source = Path(profile.source).name if profile.source else "(원본 파일 없음)"
        lines = [
            f"프로파일 ID: {profile.id}",
            f"벤더/모델/펌웨어: {profile.vendor} / {profile.model} / {profile.firmware}",
            f"원본: {source}",
            f"설명: {description}",
            "",
            "변수",
        ]
        if profile.variables:
            for variable in profile.variables.values():
                attrs = ["필수" if variable.required else "선택"]
                if variable.default is not None:
                    attrs.append(f"기본값={variable.default}")
                attrs.append(f"타입={variable.type}")
                note = variable.description_ko or variable.description
                suffix = f" - {note}" if note else ""
                lines.append(f"- {variable.name} ({', '.join(attrs)}){suffix}")
        else:
            lines.append("- 변수 없음")
        lines.extend(["", "블록"])
        if profile.blocks:
            lines.extend(f"- {block.name}" for block in profile.blocks)
        else:
            lines.append("- 블록 없음")
        self.profile_detail_view.setPlainText("\n".join(lines))

    def _selected_profile_ids(self) -> list[str]:
        selected_rows = sorted({index.row() for index in self.profile_table.selectedIndexes()})
        if not selected_rows and self.profile_table.rowCount():
            selected_rows = [0]
        profile_ids: list[str] = []
        for row in selected_rows:
            profile_id = self._profile_id_for_row(row)
            if profile_id and profile_id not in profile_ids:
                profile_ids.append(profile_id)
        return profile_ids

    def _selected_profile_id(self) -> str:
        profile_ids = self._selected_profile_ids()
        return profile_ids[0] if profile_ids else ""

    def _selected_profile(self) -> Profile | None:
        profile_id = self._selected_profile_id()
        return self._profiles.get(profile_id) if profile_id else None

    def _profile_id_for_row(self, row: int) -> str:
        item = self.profile_table.item(row, 0)
        if not item:
            return ""
        data = item.data(Qt.UserRole)
        if isinstance(data, dict):
            return str(data.get("profile_id", "") or "")
        return item.text()

    def _row_for_profile_id(self, profile_id: str) -> int | None:
        for row in range(self.profile_table.rowCount()):
            if self._profile_id_for_row(row) == profile_id:
                return row
        return None

    def _skip_blocks(self) -> set[str]:
        skipped: set[str] = set()
        for row in range(self.block_list.count()):
            item = self.block_list.item(row)
            if item.checkState() == Qt.Checked:
                skipped.add(item.text())
        return skipped

    def _pick_device_values(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "장비값 파일 선택", "", "Device Values (*.csv *.xlsx)")
        if path:
            self._device_values_path = path
            self.device_values_path_label.setText(path)
            self._last_result = None
            self._current_render_index = 0
            self._fill_result_table()
            self.preview.clear()
            self._update_action_states()

    def _render(self) -> None:
        if not self._device_values_path:
            QMessageBox.information(self, "설정 생성", "장비값 CSV/XLSX 파일을 선택하세요.")
            return
        try:
            result = self.service.render_file(self._device_values_path, skip_blocks=self._skip_blocks())
        except Exception as exc:
            QMessageBox.warning(self, "CLI 생성 실패", str(exc))
            return
        self._last_result = result
        issues = [*result.profile_issues, *result.device_issues]
        if issues:
            self.issue_view.setPlainText("\n".join(self._format_issue(issue) for issue in issues))
        else:
            self.issue_view.setPlainText(f"생성 가능 장비: {len(result.rendered)}대")
        self._fill_result_table()
        self._select_rendered(0)
        self._update_action_states()

    def _fill_result_table(self) -> None:
        rendered = self._last_result.rendered if self._last_result else []
        self.result_table.setRowCount(len(rendered))
        for row, config in enumerate(rendered):
            values = [
                config.display_name or config.device_id,
                config.profile_id,
                "생성 완료",
                str(len(config.text)),
            ]
            for column, value in enumerate(values):
                self.result_table.setItem(row, column, QTableWidgetItem(value))
        self.result_table.resizeColumnsToContents()
        self._update_action_states()

    def _select_result_from_table(self) -> None:
        rows = sorted({index.row() for index in self.result_table.selectedIndexes()})
        if rows:
            self._select_rendered(rows[0])

    def _select_rendered(self, index: int) -> None:
        if not self._last_result or not self._last_result.rendered:
            self.preview.clear()
            self._update_action_states()
            return
        self._current_render_index = max(0, min(index, len(self._last_result.rendered) - 1))
        config = self._last_result.rendered[self._current_render_index]
        self.preview.setPlainText(config.text)
        self.result_table.selectRow(self._current_render_index)
        self._update_action_states()

    def _update_action_states(self) -> None:
        has_device_values = bool(self._device_values_path)
        has_rendered = bool(self._last_result and self._last_result.rendered)
        has_bundle = bool(self._last_result and self._last_result.bundle_text)
        has_profile = self._selected_profile() is not None

        self.render_button.setEnabled(has_device_values)
        self.copy_button.setEnabled(has_rendered)
        self.copy_next_button.setEnabled(has_rendered)
        self.save_button.setEnabled(has_bundle)
        self.save_each_button.setEnabled(has_rendered)
        self.edit_profile_button.setEnabled(has_profile)

    def _copy_selected(self) -> None:
        text = self.preview.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "CLI 복사", "복사할 CLI가 없습니다.")
            return
        QApplication.clipboard().setText(text)
        self.issue_view.appendPlainText("선택 장비 CLI를 클립보드에 복사했습니다.")

    def _copy_and_next(self) -> None:
        self._copy_selected()
        if self._last_result and self._current_render_index < len(self._last_result.rendered) - 1:
            self._select_rendered(self._current_render_index + 1)

    def _save_bundle(self) -> None:
        if not self._last_result or not self._last_result.bundle_text:
            QMessageBox.information(self, "TXT 저장", "먼저 CLI를 생성하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "CLI TXT 저장", "generated_configs.txt", "Text Files (*.txt)")
        if not path:
            return
        saved = self.service.save_bundle(self._last_result.bundle_text, path)
        QMessageBox.information(self, "TXT 저장", f"저장 완료: {saved}")
        os.startfile(str(saved.parent))

    def _save_each(self) -> None:
        if not self._last_result or not self._last_result.rendered:
            QMessageBox.information(self, "장비별 TXT 저장", "먼저 CLI를 생성하세요.")
            return
        directory = QFileDialog.getExistingDirectory(self, "장비별 TXT 저장 폴더 선택")
        if not directory:
            return
        paths = self.service.save_each(self._last_result.rendered, directory)
        QMessageBox.information(self, "장비별 TXT 저장", f"{len(paths)}개 파일을 저장했습니다.")
        os.startfile(directory)

    def _open_selected_profile_editor(self) -> None:
        profile = self._selected_profile()
        if not profile:
            QMessageBox.information(self, "프로파일 편집", "편집할 프로파일을 먼저 선택하세요.")
            return
        try:
            from netops_suite.modules.config_builder.switch_configurator.profile_builder_dialog import ProfileBuilderDialog
        except Exception as exc:
            QMessageBox.warning(self, "프로파일 편집", str(exc))
            return
        dialog = ProfileBuilderDialog(self.service.profiles_dir, profile, self)
        if dialog.exec():
            self._refresh_profiles(dialog.saved_profile_id or profile.id)

    def _open_full_editor(self) -> None:
        path = Path(self._device_values_path) if self._device_values_path else None
        profile = self._selected_profile()
        self._open_advanced_editor(device_values_path=path, profile_id=profile.id if profile else "")

    def _open_advanced_editor(self, device_values_path: Path | None = None, profile_id: str = "") -> None:
        try:
            from netops_suite.modules.config_builder.switch_configurator.desktop_impl import DesktopWindow
        except Exception as exc:
            QMessageBox.warning(self, "고급 편집기", str(exc))
            return
        self._builder_window = DesktopWindow(profiles_dir=self.service.profiles_dir)
        if profile_id and hasattr(self._builder_window, "add_profile_combo"):
            self._builder_window.add_profile_combo.setCurrentText(profile_id)
        if device_values_path and hasattr(self._builder_window, "load_device_file"):
            try:
                self._builder_window.load_device_file(Path(device_values_path))
            except Exception:
                pass
        self._builder_window.show()
        self._builder_window.raise_()
        self._builder_window.activateWindow()

    @staticmethod
    def _format_issue(issue) -> str:
        row = f" row={issue.row_number}" if getattr(issue, "row_number", None) else ""
        profile = f" 프로파일={issue.profile_id}" if getattr(issue, "profile_id", "") else ""
        source = f" source={Path(issue.source).name}" if getattr(issue, "source", "") else ""
        return f"[{issue.level}] {issue.scope}{row}{profile}{source}: {issue.message}"
