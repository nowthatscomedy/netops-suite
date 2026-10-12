"""Show diagnostic results across the whole work area instead of small docks."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from app.ui.common.disclosure import add_page_header_action, make_page_header
from netops_suite.ui.actions import ActionKind, make_action_button


class ResultFocusView(QWidget):
    """A full work-area page that hosts one or more result panels as tabs.

    Panels are borrowed from their own screens and handed back unchanged, so
    running jobs keep updating the same table.
    """

    return_requested = Signal(str)
    return_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultFocusView")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        header = make_page_header(
            "결과 크게 보기",
            "결과를 작업 영역 전체에 펼쳐 봅니다. 돌아가면 결과 표가 원래 화면의 같은 자리로 갑니다.",
            help_button=False,
        )
        self.back_button = make_action_button(
            "원래 화면으로",
            ActionKind.CANCEL,
            tooltip="결과 표를 원래 화면으로 돌려놓습니다 (Esc)",
            object_name="resultFocusBackButton",
        )
        self.back_button.clicked.connect(self.return_all_requested.emit)
        add_page_header_action(header, self.back_button)
        layout.addWidget(header)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        self.tab_bar = QTabBar()
        self.tab_bar.setObjectName("resultFocusTabs")
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setAccessibleName("크게 보는 결과")
        self.tab_bar.tabCloseRequested.connect(self._request_return_at)
        tab_row.addWidget(self.tab_bar)
        tab_row.addStretch(1)
        layout.addLayout(tab_row)

        self.stack = QStackedWidget()
        self.stack.setObjectName("resultFocusStack")
        layout.addWidget(self.stack, 1)
        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)

        self._keys: list[str] = []
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.return_all_requested.emit)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def has_panel(self, key: str) -> bool:
        return key in self._keys

    def current_key(self) -> str:
        index = self.tab_bar.currentIndex()
        return self._keys[index] if 0 <= index < len(self._keys) else ""

    def add_panel(self, key: str, title: str, panel: QWidget) -> None:
        if key in self._keys:
            self.select(key)
            return
        self._keys.append(key)
        self.stack.addWidget(panel)
        self.tab_bar.addTab(title)
        panel.show()
        self.select(key)

    def select(self, key: str) -> None:
        if key in self._keys:
            self.tab_bar.setCurrentIndex(self._keys.index(key))

    def take_panel(self, key: str) -> QWidget | None:
        if key not in self._keys:
            return None
        index = self._keys.index(key)
        panel = self.stack.widget(index)
        self.stack.removeWidget(panel)
        self.tab_bar.removeTab(index)
        self._keys.pop(index)
        if panel is not None:
            panel.setParent(None)
        return panel

    def _request_return_at(self, index: int) -> None:
        if 0 <= index < len(self._keys):
            self.return_requested.emit(self._keys[index])
