from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
)

from app.app_state import AppState
from app.models.update_models import DownloadedUpdate, UpdateCheckResult
from app.ui.common import JobRunner
from app.ui.tabs.artifacts_tab import ArtifactsTab
from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from app.ui.tabs.diagnostics_tab import DiagnosticsTab
from app.ui.tabs.inspector_tab import InspectorTab
from app.ui.tabs.interface_tab import InterfaceTab
from app.ui.tabs.settings_tab import SettingsTab
from app.ui.tabs.wireless_tab import WirelessTab
from app.utils.admin import relaunch_as_admin
from app.utils.app_icon import load_app_icon
from app.version import __version__


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._job_runner = JobRunner(self.state.thread_pool, self)
        self._active_workers = self._job_runner._active_workers
        self._update_busy = False
        self._startup_activated = False
        self.setWindowTitle("NetOps Suite")
        self._apply_window_icon()
        self.resize(1120, 760)
        self.setDockOptions(
            QMainWindow.AnimatedDocks
            | QMainWindow.AllowNestedDocks
            | QMainWindow.AllowTabbedDocks
            | QMainWindow.GroupedDragging
        )

        self._build_ui()
        self._connect_signals()
        self._restore_ui_state()
        QTimer.singleShot(1200, self._maybe_check_updates_on_startup)

    def _apply_window_icon(self) -> None:
        app = QApplication.instance()
        icon = app.windowIcon() if app and not app.windowIcon().isNull() else load_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)

    def _build_ui(self) -> None:
        self.tab_widget = QTabWidget()
        self.interface_tab = InterfaceTab(self.state)
        self.diagnostics_tab = DiagnosticsTab(self.state)
        self.wireless_tab = WirelessTab(self.state)
        self.inspector_tab = InspectorTab(self.state)
        self.config_builder_tab = ConfigBuilderTab(self.state)
        self.artifacts_tab = ArtifactsTab(self.state)
        self.settings_tab = SettingsTab(self.state)

        self.tab_widget.addTab(self.interface_tab, "인터페이스")
        self.tab_widget.addTab(self.diagnostics_tab, "진단")
        self.tab_widget.addTab(self.wireless_tab, "무선 상태")
        self.tab_widget.addTab(self.inspector_tab, "장비 점검")
        self.tab_widget.addTab(self.config_builder_tab, "설정 생성")
        self.tab_widget.addTab(self.artifacts_tab, "작업 기록")
        self.tab_widget.addTab(self.settings_tab, "설정")
        self.setCentralWidget(self.tab_widget)

        toolbar = QToolBar("메인 도구", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.restart_admin_action = toolbar.addAction("관리자 권한으로 다시 실행")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_dock = QDockWidget("애플리케이션 로그", self)
        self.log_dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.log_dock.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setMinimumHeight(120)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)

        self.view_menu = QMenu("보기", self)
        self.toggle_log_view_action = QAction("애플리케이션 로그", self)
        self.toggle_log_view_action.setCheckable(True)
        self.ping_result_view_action = QAction("Ping 결과 창", self)
        self.ping_result_view_action.setCheckable(True)
        self.tcp_result_view_action = QAction("TCPing 결과 창", self)
        self.tcp_result_view_action.setCheckable(True)
        self.view_menu.addAction(self.toggle_log_view_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.ping_result_view_action)
        self.view_menu.addAction(self.tcp_result_view_action)

        self.view_button = QToolButton(self)
        self.view_button.setText("보기")
        self.view_button.setPopupMode(QToolButton.InstantPopup)
        self.view_button.setMenu(self.view_menu)
        self.view_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        toolbar.addWidget(self.view_button)

        self.log_dock.hide()

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self.admin_status_label = QLabel()
        self._update_admin_status()
        status_bar.addPermanentWidget(self.admin_status_label)
        status_bar.showMessage(f"준비 - v{__version__}")

    def _connect_signals(self) -> None:
        self.restart_admin_action.triggered.connect(self._restart_as_admin)
        self.tab_widget.currentChanged.connect(self._handle_main_tab_changed)
        self.toggle_log_view_action.toggled.connect(self._set_log_dock_visible)
        self.ping_result_view_action.toggled.connect(
            lambda checked: self.diagnostics_tab.set_result_dock_visible("ping", checked)
        )
        self.tcp_result_view_action.toggled.connect(
            lambda checked: self.diagnostics_tab.set_result_dock_visible("tcp", checked)
        )
        self.settings_tab.check_updates_requested.connect(lambda config: self._check_for_updates(config, manual=True))

        self.state.log_message.connect(self.log_view.appendPlainText)
        self.interface_tab.status_message.connect(self.statusBar().showMessage)
        self.state.config_reloaded.connect(self._update_admin_status)
        self.diagnostics_tab.result_dock_visibility_changed.connect(self._sync_result_dock_action)
        self.log_dock.topLevelChanged.connect(self._sync_log_dock_state)
        self.log_dock.visibilityChanged.connect(self._sync_log_dock_state)

        self._sync_result_dock_action("ping", self.diagnostics_tab.is_result_dock_visible("ping"))
        self._sync_result_dock_action("tcp", self.diagnostics_tab.is_result_dock_visible("tcp"))
        self._sync_log_dock_state()

    def _update_admin_status(self) -> None:
        text = "관리자 권한: 사용 중" if self.state.is_admin else "관리자 권한: 미사용"
        color = "#1b5e20" if self.state.is_admin else "#b71c1c"
        self.admin_status_label.setText(text)
        self.admin_status_label.setStyleSheet(f"color:{color}; font-weight:bold;")

    def _restart_as_admin(self) -> None:
        if self.state.is_admin:
            QMessageBox.information(self, "안내", "이미 관리자 권한으로 실행 중입니다.")
            return
        if relaunch_as_admin():
            self.close()
            return
        QMessageBox.warning(self, "실행 실패", "관리자 권한 요청이 취소되었거나 실행에 실패했습니다.")

    def _sync_log_dock_state(self) -> None:
        shown_state = not self.log_dock.isHidden()
        self.toggle_log_view_action.blockSignals(True)
        self.toggle_log_view_action.setChecked(shown_state)
        self.toggle_log_view_action.blockSignals(False)
        self.log_dock.setMaximumHeight(16777215 if self.log_dock.isFloating() else 180)

    def _set_log_dock_visible(self, visible: bool) -> None:
        self.log_dock.setVisible(visible)
        if visible:
            self.log_dock.show()
            self.log_dock.raise_()

    def _sync_result_dock_action(self, key: str, visible: bool) -> None:
        action = self.ping_result_view_action if key == "ping" else self.tcp_result_view_action
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)

    def _restore_ui_state(self) -> None:
        ui_state = self.state.get_ui_state()
        window_state = ui_state.get("main_window", {})

        self.interface_tab.restore_ui_state(ui_state.get("interface_tab", {}))
        self.diagnostics_tab.restore_ui_state(ui_state.get("diagnostics_tab", {}))
        self.wireless_tab.restore_ui_state(ui_state.get("wireless_tab", {}))

        main_tab_index = int(window_state.get("current_tab", 0) or 0)
        if 0 <= main_tab_index < self.tab_widget.count():
            self.tab_widget.setCurrentIndex(main_tab_index)

        log_visible = bool(window_state.get("log_dock_visible", False))
        ping_result_visible = bool(window_state.get("ping_result_dock_visible", False))
        tcp_result_visible = bool(window_state.get("tcp_result_dock_visible", False))

        self._set_log_dock_visible(log_visible)
        self.diagnostics_tab.set_result_dock_visible("ping", ping_result_visible)
        self.diagnostics_tab.set_result_dock_visible("tcp", tcp_result_visible)
        self._sync_log_dock_state()
        self._sync_result_dock_action("ping", ping_result_visible)
        self._sync_result_dock_action("tcp", tcp_result_visible)

    def activate_startup_loading(self) -> None:
        if self._startup_activated:
            return
        self._startup_activated = True
        QTimer.singleShot(0, self._start_visible_tab_initial_load)

    def _handle_main_tab_changed(self, index: int) -> None:
        if not self._startup_activated:
            return
        self._start_tab_initial_load(index)

    def _start_visible_tab_initial_load(self) -> None:
        self._start_tab_initial_load(self.tab_widget.currentIndex())

    def _start_tab_initial_load(self, index: int) -> None:
        if index == 0:
            self.interface_tab.start_initial_refresh()
            return
        if index == 1:
            self.diagnostics_tab.start_initial_refresh()
            return
        if index == 2:
            self.wireless_tab.start_initial_refresh()
            return

    def _save_ui_state(self) -> None:
        config = dict(self.state.app_config)
        config["ui_state"] = {
            "main_window": {
                "current_tab": self.tab_widget.currentIndex(),
                "log_dock_visible": not self.log_dock.isHidden(),
                "ping_result_dock_visible": self.diagnostics_tab.is_result_dock_visible("ping"),
                "tcp_result_dock_visible": self.diagnostics_tab.is_result_dock_visible("tcp"),
            },
            "interface_tab": self.interface_tab.save_ui_state(),
            "diagnostics_tab": self.diagnostics_tab.save_ui_state(),
            "wireless_tab": self.wireless_tab.save_ui_state(),
        }
        self.state.save_app_config(config)

    def _maybe_check_updates_on_startup(self) -> None:
        update_config = dict(self.state.app_config.get("update", {}) or {})
        if not update_config.get("check_on_startup", True):
            return
        self._check_for_updates(update_config, manual=False)

    def _check_for_updates(self, update_config: dict, manual: bool) -> None:
        if self._update_busy:
            if manual:
                QMessageBox.information(self, "업데이트 확인", "이미 업데이트 작업이 진행 중입니다.")
            return

        self._update_busy = True
        self.settings_tab.set_update_busy(True)
        self.settings_tab.set_update_status("업데이트를 확인하는 중입니다...")
        self.statusBar().showMessage("GitHub 업데이트 확인 중...")

        self._start_worker(
            self.state.update_service.check_for_updates,
            __version__,
            dict(update_config),
            on_progress=self._handle_update_progress,
            on_result=lambda result, manual=manual: self._handle_update_check_result(result, manual),
            on_finished=self._finish_update_check,
            on_error=lambda text, manual=manual: self._handle_update_error(text, manual, "업데이트 확인 실패"),
        )

    def _finish_update_check(self) -> None:
        self._update_busy = False
        self.settings_tab.set_update_busy(False)

    def _handle_update_progress(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        message = str(event.get("message", "") or "")
        if message:
            self.settings_tab.set_update_status(message)
            self.statusBar().showMessage(message)

    def _handle_update_error(self, text: str, manual: bool, title: str) -> None:
        self.settings_tab.set_update_status(title, text)
        self.statusBar().showMessage(title)
        if manual:
            QMessageBox.warning(self, title, text)

    def _handle_update_check_result(self, result: UpdateCheckResult, manual: bool) -> None:
        details_lines = []
        if result.release_name:
            details_lines.append(f"릴리즈: {result.release_name}")
        if result.latest_version:
            details_lines.append(f"최신 버전: {result.latest_version}")
        if result.is_prerelease:
            details_lines.append("배포 종류: 사전 배포")
        if result.published_at:
            details_lines.append(f"게시일: {result.published_at}")
        if result.release_url:
            details_lines.append(f"링크: {result.release_url}")
        if result.details:
            details_lines.extend(["", result.details])
        if result.body:
            details_lines.extend(["", "[릴리즈 노트]", result.body.strip()])

        details_text = "\n".join(details_lines).strip()
        self.settings_tab.set_update_status(result.message, details_text)
        self.statusBar().showMessage(result.message)

        if result.requires_config:
            if manual:
                QMessageBox.information(self, "업데이트 설정 필요", result.message + "\n\n" + result.details)
            return
        if not result.update_available:
            if manual:
                QMessageBox.information(self, "업데이트 확인", result.message)
            return
        if not result.install_ready:
            if manual:
                QMessageBox.warning(self, "업데이트 파일 확인 필요", result.message + "\n\n" + result.details)
            return

        message_lines = [
            f"현재 버전: {result.current_version}",
            f"최신 버전: {result.latest_version}",
        ]
        if result.is_prerelease:
            message_lines.append("배포 종류: 사전 배포")
        if result.release_name:
            message_lines.append(f"릴리즈: {result.release_name}")
        if result.asset:
            message_lines.append(f"설치 파일: {result.asset.name}")
        if result.verification_source == "github_release_digest":
            message_lines.append("무결성 검증: GitHub Releases SHA-256 digest")
        elif result.verification_source == "checksum_asset":
            message_lines.append("무결성 검증: 릴리즈 체크섬 파일 SHA-256")
        message_lines.extend(
            [
                "게시자 신뢰: 설치 프로그램 실행 전 Windows 코드서명 정보를 확인하세요.",
                "",
                "다운로드 및 검증을 마치면 설치 프로그램을 실행할 수 있습니다.",
            ]
        )

        if QMessageBox.question(self, "업데이트 발견", "\n".join(message_lines)) != QMessageBox.Yes:
            return
        self._download_update(result)

    def _download_update(self, check_result: UpdateCheckResult) -> None:
        self._update_busy = True
        self.settings_tab.set_update_busy(True)
        self.settings_tab.set_update_status("업데이트 파일을 다운로드하는 중입니다...")
        self.statusBar().showMessage("업데이트 다운로드 중...")

        self._start_worker(
            self.state.update_service.download_update,
            check_result,
            on_progress=self._handle_update_progress,
            on_result=self._handle_downloaded_update,
            on_finished=self._finish_update_check,
            on_error=lambda text: self._handle_update_error(text, True, "업데이트 다운로드 실패"),
        )

    def _handle_downloaded_update(self, downloaded: DownloadedUpdate) -> None:
        details = [
            f"버전: {downloaded.version}",
            f"파일: {downloaded.asset_name}",
            f"위치: {downloaded.asset_path}",
            f"SHA-256: {downloaded.sha256}",
        ]
        if downloaded.verification_source:
            details.append(f"검증: {downloaded.verification_source}")
        details.append("게시자 신뢰: SHA-256은 파일 무결성 검증이며, 게시자 신원은 코드서명으로 별도 확인해야 합니다.")

        self.settings_tab.set_update_status("업데이트 파일 검증을 완료했습니다.", "\n".join(details))
        self.statusBar().showMessage("업데이트 파일 검증 완료")

        question = (
            "검증한 설치 프로그램을 실행할까요?\n\n"
            "실행하면 현재 프로그램을 종료하고 설치 프로그램을 시작합니다."
        )
        if QMessageBox.question(self, "업데이트 설치", question) != QMessageBox.Yes:
            return

        try:
            self.state.update_service.launch_installer(downloaded.asset_path, expected_sha256=downloaded.sha256)
        except Exception as exc:
            QMessageBox.warning(self, "설치 프로그램 실행 실패", str(exc))
            return

        self._save_ui_state()
        self.close()

    def _start_worker(
        self,
        fn: Callable,
        *args,
        on_started: Callable[[], None] | None = None,
        on_progress: Callable | None = None,
        on_result: Callable | None = None,
        on_finished: Callable | None = None,
        on_error: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        self._job_runner.start(
            fn,
            *args,
            on_started=on_started,
            on_progress=on_progress,
            on_result=on_result,
            on_finished=on_finished,
            on_error=on_error,
            **kwargs,
        )

    def _discard_worker(self, worker) -> None:
        self._job_runner._discard_worker(worker)

    def closeEvent(self, event) -> None:
        self._save_ui_state()
        super().closeEvent(event)
