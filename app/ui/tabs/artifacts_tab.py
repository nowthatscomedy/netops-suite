from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.app_state import AppState
from app.ui.common import make_empty_state, make_step_hint, make_table_item, set_table_minimums


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
        layout.addWidget(make_step_hint("작업 흐름: 최근 생성된 결과, 백업, 로그, 원본 명령 출력(raw output)을 확인합니다"))

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
        notice = QLabel("결과 파일에는 장비 IP, 설정 백업, 원본 명령 출력(raw output) 등 민감정보가 포함될 수 있습니다. 외부 공유 전 IP/계정/설정값을 확인하세요.")
        notice.setWordWrap(True)
        notice.setStyleSheet("background:#fff7ed; color:#9a3412; padding:6px; border:1px solid #fed7aa;")
        layout.addWidget(notice)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["종류", "파일", "수정 시간", "경로"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_action_states)
        set_table_minimums(self.table, 220, (1, 3))
        self.empty_label = make_empty_state(
            "아직 생성된 결과가 없습니다. 장비 점검 또는 CLI 설정 생성을 실행하면 여기에 표시됩니다."
        )
        layout.addWidget(self.empty_label)
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
            full_path = str(path)
            values = [
                kind,
                path.name,
                datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                self._short_display_path(path),
            ]
            for column, value in enumerate(values):
                tooltip = full_path if column in {1, 3} else True
                item = make_table_item(value, tooltip=tooltip)
                self.table.setItem(row, column, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.empty_label.setVisible(not files)
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
    def _short_display_path(path: Path) -> str:
        parts = path.parts
        if len(parts) <= 4:
            return str(path)
        return str(Path("...").joinpath(*parts[-3:]))

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
