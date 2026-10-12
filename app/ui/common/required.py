"""One look for required inputs across the app.

A required field gets a red ``*`` after its label, forms that have required
fields say what the mark means, and a field that blocks an action is outlined
in red until the user edits it.
"""
from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

REQUIRED_COLOR = "#d92d20"
MISSING_STYLE = (
    'QLineEdit[missingRequired="true"], QPlainTextEdit[missingRequired="true"], '
    'QTextEdit[missingRequired="true"], QComboBox[missingRequired="true"], '
    'QAbstractSpinBox[missingRequired="true"] '
    f"{{ border: 1px solid {REQUIRED_COLOR}; background: #fff5f5; }}"
)
_CLEAR_CONNECTED = "_netopsMissingClearConnected"


def required_label(text: str, required: bool = True) -> QLabel:
    label = QLabel()
    set_required_label(label, text, required)
    return label


def set_required_label(label: QLabel, text: str, required: bool) -> None:
    label.setObjectName("requiredFieldLabel" if required else "optionalFieldLabel")
    if required:
        label.setText(f'{text} <span style="color:{REQUIRED_COLOR}">*</span>')
        label.setAccessibleName(f"{text} (필수)")
    else:
        label.setText(text)
        label.setAccessibleName(text)


def required_legend_text(missing: Iterable[str] | None = None) -> str:
    names = [name for name in (missing or []) if name]
    if names:
        return (
            f'<span style="color:{REQUIRED_COLOR}">필수 칸을 채워 주세요: '
            f'{", ".join(names)}</span>'
        )
    return f'<span style="color:{REQUIRED_COLOR}">*</span> 표시는 필수 입력입니다.'


def make_required_legend() -> QLabel:
    label = QLabel(required_legend_text())
    label.setObjectName("requiredLegend")
    label.setWordWrap(True)
    return label


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _edit_signal(widget: QWidget):
    if isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)):
        return widget.textChanged
    if isinstance(widget, QComboBox):
        return widget.currentTextChanged
    if isinstance(widget, QAbstractSpinBox):
        return getattr(widget, "valueChanged", None)
    return None


def mark_missing(widget: QWidget | None, missing: bool = True) -> None:
    """Outline a field that needs input; the outline clears on the next edit."""
    if widget is None:
        return
    if bool(widget.property("missingRequired")) != missing:
        widget.setProperty("missingRequired", missing)
        _repolish(widget)
    if missing and not widget.property(_CLEAR_CONNECTED):
        signal = _edit_signal(widget)
        if signal is not None:
            signal.connect(lambda *_args, field=widget: mark_missing(field, False))
            widget.setProperty(_CLEAR_CONNECTED, True)


def is_blank(widget: QWidget) -> bool:
    if isinstance(widget, QLineEdit):
        return not widget.text().strip()
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return not widget.toPlainText().strip()
    if isinstance(widget, QComboBox):
        return not widget.currentText().strip()
    return False


def check_required(
    parent: QWidget,
    fields: Iterable[tuple[QWidget, str]],
    legend: QLabel | None = None,
    *,
    title: str = "입력 확인",
) -> bool:
    """Outline empty required fields and say which; True when all are filled."""
    missing = [(widget, name) for widget, name in fields if is_blank(widget)]
    for widget, _name in missing:
        mark_missing(widget)
    if not missing:
        if legend is not None:
            legend.setText(required_legend_text())
        return True
    names = [name for _widget, name in missing]
    if legend is not None:
        legend.setText(required_legend_text(names))
    else:
        QMessageBox.warning(parent, title, f"필수 칸을 채워 주세요: {', '.join(names)}")
    missing[0][0].setFocus()
    return False
