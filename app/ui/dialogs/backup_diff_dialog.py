from __future__ import annotations

from html import escape

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QTextEdit, QVBoxLayout

from app.ui.common import make_dialog_intro, polish_dialog
from netops_suite.modules.inspector.backup_compare import (
    BackupComparison,
    backup_diff_lines,
)
from netops_suite.ui.actions import ActionKind, make_action_button

_LINE_STYLES = {
    "+": "background:#e9f7ec; color:#14532d;",
    "-": "background:#fdecec; color:#7f1d1d;",
    "@": "background:#eef3fc; color:#1e3a8a;",
}


def diff_html(lines: list[str]) -> str:
    rows: list[str] = []
    for line in lines:
        style = ""
        if line.startswith(("+++", "---")):
            style = "color:#475569; font-weight:600;"
        elif line[:1] in _LINE_STYLES:
            style = _LINE_STYLES[line[:1]]
        rows.append(f'<span style="{style}">{escape(line) or "&nbsp;"}</span>')
    return "<pre style='margin:0'>" + "\n".join(rows) + "</pre>"


class BackupDiffDialog(QDialog):
    """Show what changed in one device's configuration since its last backup."""

    def __init__(self, device_label: str, comparison: BackupComparison, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"백업 변경 내용 - {device_label}")
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        polish_dialog(self, layout)
        previous = comparison.previous_path.parent.name if comparison.previous_path else "-"
        current = comparison.current_path.parent.name if comparison.current_path else "-"
        layout.addWidget(
            make_dialog_intro(
                f"이전 백업({previous})과 이번 백업({current})을 비교했습니다. "
                "초록 줄(+)은 새로 생긴 설정, 빨간 줄(-)은 없어진 설정입니다. "
                "백업할 때마다 바뀌는 시각 표시 줄은 비교에서 뺐습니다."
            )
        )
        self.diff_view = QTextEdit()
        self.diff_view.setObjectName("backupDiffView")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lines = backup_diff_lines(comparison)
        if lines:
            self.diff_view.setHtml(diff_html(lines))
        else:
            self.diff_view.setPlainText("달라진 줄이 없습니다.")
        layout.addWidget(self.diff_view, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = make_action_button("닫기", ActionKind.CANCEL)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
