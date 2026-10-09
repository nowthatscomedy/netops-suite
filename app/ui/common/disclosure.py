"""Progressive disclosure shared by the task workspaces."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel,
    QLayout, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)


class CollapsibleSection(QWidget):
    expandedChanged = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._watched: list[tuple[QWidget, object, str | None]] = []
        self.setObjectName("collapsibleSection")
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("disclosureToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_button.setText(title)
        self.toggle_button.setAccessibleName(title)
        self.toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.toggle_button)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        layout.addWidget(self.content)
        self.content.hide()
        self.toggle_button.toggled.connect(self._toggle)

    def _toggle(self, expanded: bool) -> None:
        self.content.setVisible(expanded)
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.updateGeometry()
        self.layout().invalidate()
        self.updateGeometry()
        self.expandedChanged.emit(expanded)

    def setExpanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(expanded)

    def isExpanded(self) -> bool:
        return self.toggle_button.isChecked()

    @staticmethod
    def _value(widget: QWidget):
        if isinstance(widget, QComboBox):
            return widget.currentData() if widget.currentData() is not None else widget.currentText()
        if isinstance(widget, QAbstractButton):
            return widget.isChecked()
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        raise TypeError(f"Unsupported option widget: {type(widget).__name__}")

    def watch(self, widget: QWidget, *, empty_value: str | None = None) -> None:
        """Watch an option after its factory default has been assigned."""
        value = self._value(widget)
        if value == "" and empty_value is not None:
            value = empty_value
        self._watched.append((widget, value, empty_value))
        if isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(self._update_count)
        elif isinstance(widget, QAbstractButton):
            widget.toggled.connect(self._update_count)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.valueChanged.connect(self._update_count)
        else:
            widget.textChanged.connect(self._update_count)
        self._update_count()

    def _update_count(self, *_args) -> None:
        def effective_value(widget: QWidget, empty_value: str | None):
            value = self._value(widget)
            return empty_value if value == "" and empty_value is not None else value
        changed = sum(effective_value(widget, empty_value) != default for widget, default, empty_value in self._watched)
        suffix = f" · 변경된 옵션 {changed}개" if changed else ""
        self.toggle_button.setText(self._title + suffix)
        self.toggle_button.setAccessibleDescription(
            f"변경된 옵션 {changed}개. 펼쳐서 확인할 수 있습니다." if changed else "선택하면 세부 옵션을 표시합니다."
        )


def make_page_header(title: str, description: str, *, help_button: bool = True) -> QWidget:
    header = QWidget()
    header.setObjectName("pageHeader")
    row = QHBoxLayout(header)
    row.setContentsMargins(0, 0, 0, 4)
    row.setSpacing(10)
    text = QVBoxLayout()
    text.setSpacing(4)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    heading.setWordWrap(True)
    text.addWidget(heading)
    if description:
        subtitle = QLabel(description)
        subtitle.setObjectName("pageDescription")
        subtitle.setWordWrap(True)
        text.addWidget(subtitle)
    row.addLayout(text, 1)
    # Page actions share one row with the help button so they always line up.
    actions = QHBoxLayout()
    actions.setSpacing(8)
    row.addLayout(actions)
    header.actions_layout = actions
    if help_button:
        actions.addWidget(_make_page_help_button(title), 0, Qt.AlignmentFlag.AlignVCenter)
    return header


def add_page_header_action(header: QWidget, widget: QWidget) -> None:
    """Place a page-level action button beside the header's help button."""
    header.actions_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)


def _make_page_help_button(title: str) -> QPushButton:
    """A visible entry to the F1 help so first-time users can find it.

    It uses the shared action-button factory so it has the same height as the
    page actions placed next to it (for example 프로파일 만들기·관리).
    """
    from netops_suite.ui.actions import ActionKind, make_action_button
    from netops_suite.ui.icons import icon

    button = make_action_button(
        "도움말",
        ActionKind.SECONDARY,
        tooltip="이 화면을 단계별로 설명하는 짧은 안내를 엽니다 (F1)",
        object_name="pageHelpButton",
    )
    button.setIcon(icon("circle-question-mark", "#2457c5", 16))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAccessibleName(f"{title} 도움말 열기")
    button.clicked.connect(lambda _checked=False: _open_context_help(button))
    return button


def _open_context_help(source: QWidget) -> None:
    from PySide6.QtWidgets import QApplication

    candidates = [source.window(), *QApplication.topLevelWidgets()]
    for window in candidates:
        show_help = getattr(window, "show_context_help", None)
        if callable(show_help):
            show_help()
            return
