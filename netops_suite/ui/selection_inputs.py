from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox


class NoWheelComboBox(QComboBox):
    """Selection input that keeps wheel scrolling on the surrounding page."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("netopsChevron", True)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )
        if not arrow_rect.isValid() or arrow_rect.isEmpty():
            return

        center = arrow_rect.center()
        points_up = self.view().isVisible()
        vertical_offset = 1.5 if points_up else -1.5
        tip_offset = -1.5 if points_up else 1.5
        path = QPainterPath()
        path.moveTo(center.x() - 3.5, center.y() + vertical_offset)
        path.lineTo(center.x(), center.y() + tip_offset)
        path.lineTo(center.x() + 3.5, center.y() + vertical_offset)

        color = QColor("#475467" if self.isEnabled() else "#98a2b3")
        if self.underMouse() and self.isEnabled():
            color = QColor("#111827")
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
