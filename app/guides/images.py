"""Sharp guide screenshots: smooth pre-scaling and a full-size viewer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

ZOOM_SCHEME = "zoom"


def register_scaled_image(
    document: QTextDocument,
    base_dir: Path,
    source: str,
    width: int,
    device_pixel_ratio: float = 1.0,
) -> tuple[str, int, int]:
    """Add a smoothly down-scaled copy of ``source`` to ``document``.

    Qt's rich text scales images with a fast, blocky filter, which made the
    full-window screenshots unreadable. Returns ``(url, width, height)`` in
    logical pixels; the original ``source`` is returned unchanged when the
    file cannot be read.
    """
    path = (base_dir / source).resolve(strict=False)
    image = QImage(str(path))
    if image.isNull() or width <= 0:
        return source, 0, 0
    ratio = max(1.0, float(device_pixel_ratio or 1.0))
    target_width = min(width, image.width())
    target_height = round(image.height() * target_width / image.width())
    physical_width = round(target_width * ratio)
    if physical_width < image.width():
        image = image.scaledToWidth(physical_width, Qt.TransformationMode.SmoothTransformation)
    image.setDevicePixelRatio(image.width() / target_width)
    url = f"netops-guide-image://{target_width}/{source}"
    document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(url), image)
    return url, target_width, target_height


def image_path_from_zoom_url(url: QUrl, base_dir: Path) -> Path | None:
    if url.scheme() != ZOOM_SCHEME:
        return None
    source = url.path() or url.toString().split(":", 1)[-1]
    path = (base_dir / source.lstrip("/")).resolve(strict=False)
    try:
        path.relative_to(base_dir.resolve(strict=False))
    except ValueError:
        return None
    return path if path.is_file() else None


class ImageZoomDialog(QDialog):
    """Show a guide screenshot at its real size, scrolling when needed."""

    def __init__(self, image_path: Path, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("guideImageZoomDialog")
        self.setWindowTitle(title or "화면 크게 보기")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(image_path))
        self.image_label.setPixmap(pixmap)
        self.image_label.setAccessibleName(title or "도움말 화면 그림")
        scroll = QScrollArea()
        scroll.setWidget(self.image_label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = pixmap.width() + 40
        height = pixmap.height() + 80
        if available is not None:
            width = min(width, int(available.width() * 0.95))
            height = min(height, int(available.height() * 0.92))
        self.resize(max(480, width), max(360, height))
