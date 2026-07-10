from __future__ import annotations

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSpinBox


class NoWheelSpinBox(QSpinBox):
    """Integer input that leaves mouse-wheel scrolling to its parent view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Decimal input with the same scroll-safe behavior as NoWheelSpinBox."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()
