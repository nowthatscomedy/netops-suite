from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap


APP_USER_MODEL_ID = "handreamnet.SwitchConfigBuilderDesktop"
_APP_ICON_CACHE: QIcon | None = None


def set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        return


def build_app_icon() -> QIcon:
    global _APP_ICON_CACHE
    if _APP_ICON_CACHE is not None:
        return _APP_ICON_CACHE

    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    background = QLinearGradient(24, 18, 236, 238)
    background.setColorAt(0.0, QColor("#f5efe4"))
    background.setColorAt(1.0, QColor("#e7d5b3"))
    painter.setBrush(background)
    painter.setPen(QPen(QColor("#c7b28a"), 6))
    painter.drawRoundedRect(16, 16, 224, 224, 52, 52)

    painter.setBrush(QColor("#1b1712"))
    painter.setPen(QPen(QColor("#2f261d"), 4))
    painter.drawRoundedRect(38, 58, 180, 122, 28, 28)

    painter.setPen(QPen(QColor("#c55f29"), 12, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(72, 92, 98, 112)
    painter.drawLine(72, 132, 98, 112)
    painter.drawLine(112, 136, 142, 136)

    painter.setBrush(QColor("#efe8da"))
    painter.setPen(Qt.NoPen)
    for index in range(6):
        x_pos = 58 + (index * 26)
        painter.drawRoundedRect(x_pos, 150, 16, 10, 3, 3)

    for index in range(6):
        x_pos = 60 + (index * 26)
        painter.setBrush(QColor("#2ea665") if index in {0, 2, 4} else QColor("#d6cab8"))
        painter.drawEllipse(x_pos, 170, 8, 8)

    painter.setBrush(QColor("#c55f29"))
    painter.drawRoundedRect(52, 46, 56, 10, 5, 5)

    painter.end()

    _APP_ICON_CACHE = QIcon(pixmap)
    return _APP_ICON_CACHE
