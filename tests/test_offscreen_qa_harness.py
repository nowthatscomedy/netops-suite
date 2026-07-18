from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Event
import time

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.tabs.ai_chat_tab import AiChatTab
from qa.offscreen import OffscreenQaHarness
from qa.offscreen.fakes import (
    DeterministicPingService,
    install_deterministic_services,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QA_CONFIG = PROJECT_ROOT / "qa" / "offscreen" / "scenarios.json"


def _wait_until(qapp, predicate, timeout_ms: int = 4000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError("Qt 비동기 상태 대기 시간이 초과되었습니다.")


def _click_list_row(list_widget, row: int) -> None:
    item = list_widget.item(row)
    rect = list_widget.visualItemRect(item)
    QTest.mouseClick(
        list_widget.viewport(),
        Qt.MouseButton.LeftButton,
        pos=rect.center(),
    )


def _paste(qapp, widget, text: str) -> None:
    qapp.clipboard().setText(text)
    widget.setFocus()
    QTest.keyClick(widget, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(widget, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)


def test_offscreen_user_flow_configuration_runs_all_scenarios(qapp, tmp_path):
    output_dir = tmp_path / "offscreen-results"
    report = OffscreenQaHarness(
        project_root=PROJECT_ROOT,
        config_path=QA_CONFIG,
        output_dir=output_dir,
    ).run()

    failures = {
        result.scenario_id: result.error
        for result in report.results
        if not result.ok
    }
    assert failures == {}
    assert len(report.results) == 15
    assert len(report.layout_checks) == 21
    assert report.json_path.is_file()
    assert report.markdown_path.is_file()
    assert len(list(output_dir.glob("*.png"))) == 15


def test_offscreen_normal_permission_flow_does_not_inherit_elevated_host(
    qapp,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("app.app_state.is_running_as_admin", lambda: True)
    config = json.loads(QA_CONFIG.read_text(encoding="utf-8"))
    config["layout_sweep_viewports"] = []
    config["scenarios"] = [
        scenario
        for scenario in config["scenarios"]
        if scenario["id"] == "interface_refresh"
    ]
    config_path = tmp_path / "interface-refresh.json"
    config_path.write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )

    report = OffscreenQaHarness(
        project_root=PROJECT_ROOT,
        config_path=config_path,
        output_dir=tmp_path / "offscreen-elevated-host",
    ).run()

    assert len(report.results) == 1
    assert report.results[0].scenario_id == "interface_refresh"
    assert report.results[0].ok


class _RealPoolPingService(DeterministicPingService):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.cancel_seen = Event()

    def run_multi_ping(self, *args, cancel_event=None, **kwargs):
        self.started.set()
        while not self.release.wait(0.01):
            if cancel_event is not None and cancel_event.is_set():
                self.cancel_seen.set()
                return []
        return super().run_multi_ping(
            *args,
            cancel_event=cancel_event,
            **kwargs,
        )

    def reset_gate(self) -> None:
        self.started.clear()
        self.release.clear()
        self.cancel_seen.clear()


def test_real_qthreadpool_ping_busy_completion_and_cancel(
    qapp,
    tmp_path,
    monkeypatch,
):
    qa_logger = logging.Logger("netops_suite.real_pool_qa")
    qa_logger.addHandler(logging.NullHandler())
    monkeypatch.setattr(
        "app.app_state.configure_logging",
        lambda *_args, **_kwargs: qa_logger,
    )
    monkeypatch.setattr(
        "app.app_state.shutdown_logging",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(AiChatTab, "refresh_provider_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        AiChatTab,
        "_ensure_model_catalog_fresh",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        MainWindow,
        "_maybe_check_updates_on_startup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )

    state = AppState(tmp_path / "runtime")
    state.app_config["update"]["check_on_startup"] = False
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    state.thread_pool = pool
    install_deterministic_services(state)
    ping_service = _RealPoolPingService()
    state.ping_service = ping_service
    window = MainWindow(state)
    window.resize(1280, 800)
    window.show()
    tab = window.diagnostics_tab

    try:
        _click_list_row(window.nav_list, 1)
        tab.select_diagnostic_tab("ping")
        _paste(qapp, tab.ping_targets_edit, "A,192.0.2.1\nB,192.0.2.2")
        QTest.mouseClick(tab.ping_start_button, Qt.MouseButton.LeftButton)

        _wait_until(qapp, ping_service.started.is_set)
        assert not tab.ping_start_button.isEnabled()
        assert tab.ping_cancel_button.isEnabled()

        ping_service.release.set()
        _wait_until(
            qapp,
            lambda: tab.ping_table.rowCount() == 2
            and tab.ping_start_button.isEnabled(),
        )
        assert {
            tab.ping_table.item(row, 1).text()
            for row in range(tab.ping_table.rowCount())
        } == {"192.0.2.1", "192.0.2.2"}

        ping_service.reset_gate()
        tab.ping_continuous_check.setChecked(True)
        QTest.mouseClick(tab.ping_start_button, Qt.MouseButton.LeftButton)
        _wait_until(qapp, ping_service.started.is_set)
        QTest.mouseClick(tab.ping_cancel_button, Qt.MouseButton.LeftButton)
        _wait_until(qapp, ping_service.cancel_seen.is_set)
        _wait_until(qapp, tab.ping_start_button.isEnabled)
        assert not tab.ping_cancel_button.isEnabled()
    finally:
        ping_service.release.set()
        pool.waitForDone(5000)
        window.shutdown()
        window.close()
        window.deleteLater()
        state.deleteLater()
        pool.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
