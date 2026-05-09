from __future__ import annotations

import ctypes
import sys

from netops_suite import APP_ID, APP_NAME
from PySide6.QtWidgets import QApplication, QMessageBox

from app.app_state import AppState
from app.main_window import MainWindow
from app.utils.app_icon import load_app_icon
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
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    state = AppState()
    window = MainWindow(state)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
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
