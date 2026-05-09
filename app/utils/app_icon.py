from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.utils.file_utils import resolve_asset_path


def app_icon_candidates() -> Iterable[Path]:
    yield resolve_asset_path("icons", "netops_toolkit.ico")
    yield resolve_asset_path("icons", "netops_toolkit_icon_preview.png")


def load_app_icon() -> QIcon:
    for candidate in app_icon_candidates():
        if not candidate.exists():
            continue
        icon = QIcon(str(candidate))
        if not icon.isNull():
            return icon

    return _build_fallback_netops_icon()


def _build_fallback_netops_icon() -> QIcon:
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    painter.setPen(QPen(QColor("#2aa7b8"), 14, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for offset, width in ((44, 168), (76, 104)):
        path = QPainterPath()
        path.moveTo(QPointF(offset, 104))
        path.cubicTo(QPointF(82, 58), QPointF(174, 58), QPointF(256 - offset, 104))
        painter.drawPath(path)

    painter.setBrush(QColor("#12365a"))
    painter.setPen(QPen(QColor("#0d2741"), 6))
    painter.drawRoundedRect(74, 104, 108, 88, 18, 18)

    painter.setBrush(Qt.transparent)
    painter.setPen(QPen(QColor("#d8f6fb"), 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(105, 132, 105, 160)
    painter.drawLine(128, 132, 128, 160)
    painter.drawLine(151, 132, 151, 160)
    painter.drawLine(94, 126, 162, 126)

    painter.setBrush(QColor("#0d2741"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(104, 168, 48, 32, 8, 8)
    painter.drawRoundedRect(112, 198, 32, 28, 6, 6)

    painter.end()
    return QIcon(pixmap)
