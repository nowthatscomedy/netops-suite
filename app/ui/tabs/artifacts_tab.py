from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState


from netops_suite.ui.actions import ActionKind, make_action_button


class ArtifactsTab(QWidget):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._paths: list[Path] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.refresh_button = make_action_button("목록 새로고침", ActionKind.REFRESH)
        self.refresh_button.clicked.connect(self.refresh)
        self.open_file_button = make_action_button("선택 파일 열기", ActionKind.OPEN, enabled=False)
        self.open_file_button.clicked.connect(self._open_selected_file)
        self.open_folder_button = make_action_button("선택 폴더 열기", ActionKind.OPEN, enabled=False)
        self.open_folder_button.clicked.connect(self._open_selected_folder)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.open_file_button)
        actions.addWidget(self.open_folder_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["종류", "파일", "수정 시간", "경로"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_action_states)
        layout.addWidget(self.table, 1)

    def refresh(self) -> None:
        roots = [
            self.state.paths.logs_dir,
            self.state.paths.exports_dir,
            self.state.paths.data_root / "inspector" / "runs",
            self.state.paths.data_root / "config_builder",
        ]
        files: list[Path] = []
        for root in roots:
            if root.exists():
                files.extend(path for path in root.rglob("*") if path.is_file() and path.name != ".gitkeep")
        files = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:300]
        self._paths = files
        self.table.setRowCount(len(files))
        for row, path in enumerate(files):
            kind = self._kind_for(path)
            mtime = path.stat().st_mtime
            values = [kind, path.name, str(Path(path).stat().st_mtime_ns), str(path)]
            values[2] = __import__("datetime").datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self._update_action_states()

    def _selected_path(self) -> Path | None:
        indexes = self.table.selectedIndexes()
        if not indexes:
            return None
        row = indexes[0].row()
        if 0 <= row < len(self._paths):
            return self._paths[row]
        return None

    def _open_selected_file(self) -> None:
        path = self._selected_path()
        if path:
            os.startfile(path)

    def _open_selected_folder(self) -> None:
        path = self._selected_path()
        if path:
            os.startfile(path.parent)

    def _update_action_states(self) -> None:
        has_selection = self._selected_path() is not None
        self.open_file_button.setEnabled(has_selection)
        self.open_folder_button.setEnabled(has_selection)

    @staticmethod
    def _kind_for(path: Path) -> str:
        text = str(path).lower()
        if "backup" in text:
            return "백업"
        if "session_logs" in text or path.suffix.lower() == ".log":
            return "로그"
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return "결과 Excel"
        if path.suffix.lower() == ".txt":
            return "CLI/TXT"
        return "파일"
