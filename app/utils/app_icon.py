from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtGui import QIcon

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

    try:
        from netops_suite.modules.config_builder.switch_configurator.app_icon import build_app_icon

        return build_app_icon()
    except Exception:
        return QIcon()
