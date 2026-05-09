from __future__ import annotations

from enum import Enum
from typing import Iterable

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QHBoxLayout, QPushButton, QSizePolicy, QStyle


class ActionKind(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    UTILITY = "utility"
    BROWSE = "browse"
    REFRESH = "refresh"
    OPEN = "open"
    SAVE = "save"
    EXPORT = "export"
    COPY = "copy"
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    START = "start"
    STOP = "stop"
    CANCEL = "cancel"
    DANGER = "danger"


_ICON_MAP = {
    ActionKind.BROWSE: QStyle.StandardPixmap.SP_DirOpenIcon,
    ActionKind.OPEN: QStyle.StandardPixmap.SP_DirOpenIcon,
    ActionKind.SAVE: QStyle.StandardPixmap.SP_DialogSaveButton,
    ActionKind.EXPORT: QStyle.StandardPixmap.SP_DialogSaveButton,
    ActionKind.REFRESH: QStyle.StandardPixmap.SP_BrowserReload,
    ActionKind.ADD: QStyle.StandardPixmap.SP_FileDialogNewFolder,
    ActionKind.DELETE: QStyle.StandardPixmap.SP_TrashIcon,
    ActionKind.START: QStyle.StandardPixmap.SP_MediaPlay,
    ActionKind.STOP: QStyle.StandardPixmap.SP_MediaStop,
    ActionKind.CANCEL: QStyle.StandardPixmap.SP_DialogCancelButton,
}

_PALETTE = {
    ActionKind.PRIMARY: ("#1f6feb", "#ffffff", "#1f6feb", "#1a5fd0"),
    ActionKind.START: ("#1f6feb", "#ffffff", "#1f6feb", "#1a5fd0"),
    ActionKind.SAVE: ("#1f6feb", "#ffffff", "#1f6feb", "#1a5fd0"),
    ActionKind.DANGER: ("#b42318", "#ffffff", "#b42318", "#9f1d14"),
    ActionKind.DELETE: ("#b42318", "#ffffff", "#b42318", "#9f1d14"),
    ActionKind.STOP: ("#b42318", "#ffffff", "#b42318", "#9f1d14"),
    ActionKind.CANCEL: ("#fff7ed", "#9a3412", "#fdba74", "#ffedd5"),
}


def make_action_button(
    text: str,
    kind: ActionKind | str = ActionKind.SECONDARY,
    *,
    tooltip: str | None = None,
    object_name: str | None = None,
    enabled: bool = True,
    min_width: int | None = None,
) -> QPushButton:
    action_kind = ActionKind(kind)
    button = QPushButton(text)
    button.setProperty("actionKind", action_kind.value)
    button.setMinimumHeight(28)
    button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    if min_width is not None:
        button.setMinimumWidth(min_width)
    if object_name:
        button.setObjectName(object_name)
    if tooltip:
        button.setToolTip(tooltip)
    icon = _standard_icon(action_kind)
    if icon is not None:
        button.setIcon(icon)
    button.setStyleSheet(_style_for(action_kind))
    button.setEnabled(enabled)
    return button


def make_action_row(*buttons: QPushButton, align: str = "left") -> QHBoxLayout:
    layout = QHBoxLayout()
    if align == "right":
        layout.addStretch(1)
    for button in buttons:
        layout.addWidget(button)
    if align in {"left", "split"}:
        layout.addStretch(1)
    return layout


def set_running_state(start_buttons: Iterable[QPushButton], stop_button: QPushButton, running: bool) -> None:
    for button in start_buttons:
        button.setEnabled(not running)
    stop_button.setEnabled(running)


def polish_dialog_button_box(buttons) -> None:
    ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if ok_button is not None:
        _polish_existing_button(ok_button, "저장", ActionKind.SAVE)
    if cancel_button is not None:
        _polish_existing_button(cancel_button, "취소", ActionKind.CANCEL)


def polish_existing_button(button: QPushButton, kind: ActionKind | str, *, text: str | None = None) -> QPushButton:
    return _polish_existing_button(button, text, ActionKind(kind))


def _polish_existing_button(button: QPushButton, text: str | None, kind: ActionKind) -> QPushButton:
    if text is not None:
        button.setText(text)
    button.setProperty("actionKind", kind.value)
    button.setMinimumHeight(max(button.minimumHeight(), 28))
    icon = _standard_icon(kind)
    if icon is not None:
        button.setIcon(icon)
    button.setStyleSheet(_style_for(kind))
    return button


def _standard_icon(kind: ActionKind):
    pixmap = _ICON_MAP.get(kind)
    if pixmap is None:
        return None
    app = QApplication.instance()
    if app is None:
        return None
    return app.style().standardIcon(pixmap)


def _style_for(kind: ActionKind) -> str:
    background, color, border, hover = _PALETTE.get(kind, ("#f8fafc", "#1f2937", "#cbd5e1", "#eef2f7"))
    return f"""
QPushButton {{
    background: {background};
    color: {color};
    border: 1px solid {border};
    border-radius: 5px;
    padding: 4px 10px;
    font-weight: 500;
}}
QPushButton:hover:!disabled {{
    background: {hover};
}}
QPushButton:disabled {{
    background: #f3f4f6;
    color: #9ca3af;
    border-color: #e5e7eb;
}}
"""
