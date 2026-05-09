from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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


class ConfigBuilderTab(QWidget):
    def __init__(self, state: AppState | None = None, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        user_data = state.paths.data_root / "config_builder" if state else None
        self.service = ConfigBuilderService(user_data_dir=user_data)
        self._last_result: ConfigBuilderRenderResult | None = None
        self._builder_window = None
        self._device_values_path = ""
        self._current_render_index = 0
        self._build_ui()
        self._refresh_profiles()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        action_row = QHBoxLayout()
        open_button = QPushButton("장비값 열기")
        open_button.clicked.connect(self._pick_device_values)
        sample_button = QPushButton("샘플 열기")
        sample_button.clicked.connect(self._open_sample_device_values)
        render_button = QPushButton("CLI 생성")
        render_button.clicked.connect(self._render)
        copy_button = QPushButton("선택 CLI 복사")
        copy_button.clicked.connect(self._copy_selected)
        copy_next_button = QPushButton("복사+다음")
        copy_next_button.clicked.connect(self._copy_and_next)
        save_button = QPushButton("전체 TXT 저장")
        save_button.clicked.connect(self._save_bundle)
        save_each_button = QPushButton("장비별 TXT 저장")
        save_each_button.clicked.connect(self._save_each)
        full_editor_button = QPushButton("고급 편집기 열기")
        full_editor_button.clicked.connect(self._open_full_editor)
        for button in (open_button, sample_button, render_button, copy_button, copy_next_button, save_button, save_each_button):
            action_row.addWidget(button)
        action_row.addStretch(1)
        action_row.addWidget(full_editor_button)
        layout.addLayout(action_row)

        self.device_values_path_label = QLabel("장비값 CSV/XLSX를 선택하세요.")
        self.device_values_path_label.setWordWrap(True)
        layout.addWidget(self.device_values_path_label)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("프로필"))
        self.profile_table = QTableWidget(0, 5)
        self.profile_table.setHorizontalHeaderLabels(["profile_id", "vendor", "model", "firmware", "blocks"])
        self.profile_table.horizontalHeader().setStretchLastSection(True)
        self.profile_table.itemSelectionChanged.connect(self._refresh_block_list)
        left_layout.addWidget(self.profile_table, 2)
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
        self.result_table = QTableWidget(0, 4)
        self.result_table.setHorizontalHeaderLabels(["device", "profile", "상태", "길이"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.itemSelectionChanged.connect(self._select_result_from_table)
        right_layout.addWidget(self.result_table, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right_layout.addWidget(self.preview, 3)
        splitter.addWidget(right)
        splitter.setSizes([420, 700])
        layout.addWidget(splitter, 1)

    def _refresh_profiles(self) -> None:
        profiles, issues = self.service.load_profiles()
        self.profile_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles.values()):
            values = [
                profile.id,
                profile.vendor,
                profile.model,
                profile.firmware,
                ", ".join(block.name for block in profile.blocks),
            ]
            for column, value in enumerate(values):
                self.profile_table.setItem(row, column, QTableWidgetItem(value))
        self.profile_table.resizeColumnsToContents()
        if issues:
            self.issue_view.setPlainText("\n".join(self._format_issue(issue) for issue in issues))
        else:
            self.issue_view.setPlainText(f"프로필 {len(profiles)}개를 불러왔습니다. 프로필 폴더: {self.service.profiles_dir}")
        self._refresh_block_list()

    def _refresh_block_list(self) -> None:
        self.block_list.clear()
        selected_rows = {index.row() for index in self.profile_table.selectedIndexes()}
        if not selected_rows and self.profile_table.rowCount():
            selected_rows = {0}
        blocks: list[str] = []
        for row in selected_rows:
            item = self.profile_table.item(row, 4)
            if not item:
                continue
            for block in item.text().split(","):
                block_name = block.strip()
            if block_name and block_name not in blocks:
                blocks.append(block_name)
        for block in blocks:
            item = QListWidgetItem(block)
            item.setCheckState(Qt.Unchecked)
            self.block_list.addItem(item)

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

    def _open_sample_device_values(self) -> None:
        sample = self.service.sample_device_values()
        if not sample:
            QMessageBox.information(self, "샘플 없음", "패키지에 포함된 샘플 장비값 파일을 찾지 못했습니다.")
            return
        self._device_values_path = str(sample)
        self.device_values_path_label.setText(str(sample))
        os.startfile(str(sample.parent))

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

    def _select_result_from_table(self) -> None:
        rows = sorted({index.row() for index in self.result_table.selectedIndexes()})
        if rows:
            self._select_rendered(rows[0])

    def _select_rendered(self, index: int) -> None:
        if not self._last_result or not self._last_result.rendered:
            self.preview.clear()
            return
        self._current_render_index = max(0, min(index, len(self._last_result.rendered) - 1))
        config = self._last_result.rendered[self._current_render_index]
        self.preview.setPlainText(config.text)
        self.result_table.selectRow(self._current_render_index)

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

    def _open_full_editor(self) -> None:
        try:
            from netops_suite.modules.config_builder.switch_configurator.desktop_impl import DesktopWindow
        except Exception as exc:
            QMessageBox.warning(self, "고급 편집기", str(exc))
            return
        self._builder_window = DesktopWindow()
        if self._device_values_path and hasattr(self._builder_window, "load_device_file"):
            try:
                self._builder_window.load_device_file(Path(self._device_values_path))
            except Exception:
                pass
        self._builder_window.show()

    @staticmethod
    def _format_issue(issue) -> str:
        row = f" row={issue.row_number}" if getattr(issue, "row_number", None) else ""
        profile = f" profile={issue.profile_id}" if getattr(issue, "profile_id", "") else ""
        source = f" source={Path(issue.source).name}" if getattr(issue, "source", "") else ""
        return f"[{issue.level}] {issue.scope}{row}{profile}{source}: {issue.message}"
