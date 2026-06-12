from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QLabel, QMenu, QMessageBox, QSizePolicy, QStyle, QStyleOptionButton, QToolButton


_STATUS_STYLES = {
    "info": ("#f3f4f6", "#344054", "#d0d5dd"),
    "success": ("#ecfdf3", "#166534", "#bbf7d0"),
    "warning": ("#fffbeb", "#92400e", "#fde68a"),
    "error": ("#fef2f2", "#991b1b", "#fecaca"),
}

_VISIBLE_CHECKBOX_STYLE = """
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #94a3b8;
    border-radius: 3px;
    background: #ffffff;
}
QCheckBox::indicator:hover {
    border-color: #64748b;
}
QCheckBox::indicator:checked {
    background: #475467;
    border: 1px solid #344054;
}
QCheckBox::indicator:checked:disabled {
    background: #94a3b8;
    border-color: #64748b;
}
QCheckBox::indicator:unchecked:disabled {
    background: #f1f5f9;
    border-color: #cbd5e1;
}
"""


class _VisibleCheckBox(QCheckBox):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)

        option = QStyleOptionButton()
        self.initStyleOption(option)
        indicator_rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        if not indicator_rect.isValid():
            return

        checked = self.isChecked()
        enabled = self.isEnabled()
        fill = QColor("#475467" if checked and enabled else "#94a3b8" if checked else "#ffffff")
        border = QColor("#344054" if checked and enabled else "#64748b" if checked else "#94a3b8")
        if not enabled and not checked:
            fill = QColor("#f1f5f9")
            border = QColor("#cbd5e1")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = indicator_rect.adjusted(1, 1, -1, -1)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)

        if checked:
            check_path = QPainterPath()
            check_path.moveTo(box.left() + box.width() * 0.25, box.top() + box.height() * 0.53)
            check_path.lineTo(box.left() + box.width() * 0.43, box.top() + box.height() * 0.70)
            check_path.lineTo(box.left() + box.width() * 0.76, box.top() + box.height() * 0.32)
            pen = QPen(QColor("#ffffff"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(check_path)


def ensure_visible_checkbox(checkbox: QCheckBox) -> QCheckBox:
    existing_style = checkbox.styleSheet().strip()
    checkbox.setStyleSheet(f"{existing_style}\n{_VISIBLE_CHECKBOX_STYLE}" if existing_style else _VISIBLE_CHECKBOX_STYLE)
    checkbox.setMinimumHeight(max(checkbox.minimumHeight(), 24))
    return checkbox


def make_visible_checkbox(text: str = "") -> QCheckBox:
    return ensure_visible_checkbox(_VisibleCheckBox(text))


def make_step_hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("stepHint")
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    label.setMaximumHeight(42)
    label.setStyleSheet(
        "background:transparent; color:#475467; border:0; border-left:3px solid #d0d5dd; "
        "padding:4px 0 4px 9px; font-weight:500;"
    )
    return label


def make_empty_state(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("emptyState")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    label.setStyleSheet(
        "background:transparent; color:#667085; padding:14px 12px; "
        "border:1px dashed #d0d5dd; border-radius:5px;"
    )
    return label


def make_dialog_intro(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("dialogIntro")
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    return label


def polish_dialog(dialog, layout=None) -> None:
    dialog.setObjectName("subDialog")
    dialog.setSizeGripEnabled(True)
    if layout is not None:
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)


def make_selectable_wrapped_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    return label


def make_inline_status(kind: str = "info", text: str = "") -> QLabel:
    label = QLabel()
    label.setObjectName("inlineStatus")
    label.setWordWrap(True)
    set_inline_status(label, kind, text)
    return label


def set_inline_status(label: QLabel, kind: str, text: str) -> None:
    background, color, border = _STATUS_STYLES.get(kind, _STATUS_STYLES["info"])
    label.setText(text)
    label.setStyleSheet(
        f"background:{background}; color:{color}; border:1px solid {border}; "
        "border-radius:6px; padding:7px 9px;"
    )
    label.setVisible(bool(text))


def make_menu_button(text: str, menu: QMenu, tooltip: str = "") -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setMenu(menu)
    button.setProperty("actionKind", "utility")
    button.setMinimumHeight(30)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def confirm_risky_action(
    parent,
    title: str,
    impact: str,
    reversibility: str,
    output_location: str,
    *,
    question: str = "계속 진행할까요?",
    confirm_text: str = "실행",
    cancel_text: str = "취소",
) -> bool:
    message = "\n\n".join(
        [
            f"영향 범위: {impact}",
            f"되돌리기: {reversibility}",
            f"기록 위치: {output_location}",
            question,
        ]
    )
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    yes_button = box.button(QMessageBox.StandardButton.Yes)
    no_button = box.button(QMessageBox.StandardButton.No)
    if yes_button is not None:
        yes_button.setText(confirm_text)
    if no_button is not None:
        no_button.setText(cancel_text)
    return box.exec() == QMessageBox.StandardButton.Yes
