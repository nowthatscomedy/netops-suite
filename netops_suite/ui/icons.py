"""Offline, high-DPI Lucide icons shared by the desktop workspaces."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app.utils.file_utils import resolve_asset_path


PAGE_ICONS = {
    "home": "house", "interface": "network", "diagnostics": "activity",
    "wireless": "wifi", "inspector": "server-cog",
    "config_builder": "square-terminal", "transfer": "folder-sync",
    "settings": "settings-2",
}


@lru_cache(maxsize=192)
def icon(name: str, color: str = "#52627a", size: int = 20) -> QIcon:
    """Render the bundled source SVG at 1×, 1.25×, 1.5× and 2× resolution."""
    path = resolve_asset_path("icons", "lucide", f"{name}.svg")
    if not path.is_file():
        return QIcon()
    data = path.read_bytes().replace(b"currentColor", color.encode("ascii"))
    renderer = QSvgRenderer(QByteArray(data))
    result = QIcon()
    for ratio in (1.0, 1.25, 1.5, 2.0):
        pixels = round(size * ratio)
        pixmap = QPixmap(pixels, pixels)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, pixels, pixels))
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        result.addPixmap(pixmap)
    return result
