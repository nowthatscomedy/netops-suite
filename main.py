from __future__ import annotations

import ctypes
import sys

from netops_suite import APP_ID, APP_NAME
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.app_state import AppState
from app.main_window import MainWindow
from app.utils.file_utils import resolve_asset_path
from app.version import __version__


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app_icon_path = resolve_asset_path("icons", "netops_toolkit.ico")
    if app_icon_path.exists():
        app.setWindowIcon(QIcon(str(app_icon_path)))

    state = AppState()
    window = MainWindow(state)
    if app_icon_path.exists():
        window.setWindowIcon(QIcon(str(app_icon_path)))
    window.show()
    window.activate_startup_loading()

    if not state.paths.config_dir.exists():
        QMessageBox.warning(
            window,
            "Config Error",
            "Configuration directory could not be initialized. Some features may be unavailable.",
        )

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
