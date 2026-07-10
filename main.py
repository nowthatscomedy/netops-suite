from __future__ import annotations

import ctypes
import os
import sys
import tempfile

from netops_suite import APP_ID, APP_NAME
from PySide6.QtWidgets import QApplication, QMessageBox

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.common.theme import apply_app_theme
from app.ui.startup_loading import StartupLoadingWindow
from app.utils.app_icon import load_app_icon
from app.version import __version__


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _run_release_smoke_test() -> int:
    """Initialize and tear down the packaged application without showing UI."""
    previous_data_root = os.environ.get("NETOPS_SUITE_DATA_ROOT")
    previous_project_data = os.environ.get("NETOPS_SUITE_USE_PROJECT_DATA")
    state = None
    window = None
    try:
        with tempfile.TemporaryDirectory(prefix="netops_suite_release_smoke_") as data_root:
            os.environ["NETOPS_SUITE_DATA_ROOT"] = data_root
            os.environ.pop("NETOPS_SUITE_USE_PROJECT_DATA", None)
            _set_windows_app_id()
            app = QApplication.instance() or QApplication(sys.argv)
            app.setApplicationName(APP_NAME)
            app.setOrganizationName(APP_NAME)
            app.setApplicationVersion(__version__)
            apply_app_theme(app)
            state = AppState()
            window = MainWindow(state)
            app.processEvents()
            window.shutdown()
            window = None
            state = None
            app.processEvents()
        return 0
    except Exception as exc:
        if sys.stderr is not None:
            print(f"Release smoke test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if window is not None:
            window.shutdown()
        elif state is not None:
            state.shutdown()
        if previous_data_root is None:
            os.environ.pop("NETOPS_SUITE_DATA_ROOT", None)
        else:
            os.environ["NETOPS_SUITE_DATA_ROOT"] = previous_data_root
        if previous_project_data is None:
            os.environ.pop("NETOPS_SUITE_USE_PROJECT_DATA", None)
        else:
            os.environ["NETOPS_SUITE_USE_PROJECT_DATA"] = previous_project_data


def main() -> int:
    if "--release-smoke-test" in sys.argv[1:]:
        return _run_release_smoke_test()
    if "--version" in sys.argv[1:]:
        if sys.stdout is not None:
            print(f"{APP_NAME} {__version__}")
        return 0

    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    apply_app_theme(app)
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    loading = StartupLoadingWindow(__version__)
    if not app_icon.isNull():
        loading.setWindowIcon(app_icon)
    loading.show()
    loading.set_step(0, "실행 환경 확인", "Windows 앱 식별자와 Qt 런타임을 준비했습니다.", 8)
    loading.set_step(1, "테마와 아이콘 준비", "앱 테마와 창 아이콘을 적용했습니다.", 16)

    state_progress = {"value": 20}

    def report_state_startup(message: str, detail: str = "") -> None:
        state_progress["value"] = min(state_progress["value"] + 4, 62)
        service_keywords = ("서비스", "진단", "파일 전송", "업데이트", "PowerShell")
        step = 3 if any(keyword in message for keyword in service_keywords) else 2
        loading.set_step(step, message, detail, state_progress["value"])

    window_progress = {"value": 64}

    def report_window_startup(message: str, detail: str = "") -> None:
        window_progress["value"] = min(window_progress["value"] + 3, 92)
        loading.set_step(4, message, detail, window_progress["value"])

    state = None
    window = None
    try:
        loading.set_step(2, "설정 파일 준비", "사용자 설정과 기본 프로파일을 확인합니다.", 20)
        state = AppState(startup_callback=report_state_startup)
        loading.set_step(3, "서비스 초기화 완료", "네트워크 진단과 파일 전송 기능을 사용할 준비가 됐습니다.", 62)

        window = MainWindow(state, startup_callback=report_window_startup)
        if not app_icon.isNull():
            window.setWindowIcon(app_icon)

        loading.set_step(5, "시작 데이터 갱신", "첫 화면에 필요한 네트워크 정보를 불러올 준비를 합니다.", 94)
        window.show()
        app.aboutToQuit.connect(window.shutdown)
        window.activate_startup_loading()
        loading.complete()
        loading.finish_after_minimum()

        if not state.paths.config_dir.exists():
            QMessageBox.warning(
                window,
                "Config Error",
                "Configuration directory could not be initialized. Some features may be unavailable.",
            )
    except Exception as exc:
        if window is not None:
            window.shutdown()
        elif state is not None:
            state.shutdown()
        loading.fail("시작 실패", str(exc))
        QMessageBox.critical(
            loading,
            "Startup Error",
            f"NetOps Suite를 시작할 수 없습니다.\n\n{exc}",
        )
        return 1

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
