from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Event
import time

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, Qt
from PySide6.QtGui import QImage
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
from scripts.guide_capture_fingerprint import (
    CAPTURE_MANIFEST_SCHEMA_VERSION,
    build_capture_fingerprint,
    capture_manifest_errors,
)
from scripts.run_offscreen_qa import export_guide_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QA_CONFIG = PROJECT_ROOT / "qa" / "offscreen" / "scenarios.json"


def test_codex_guide_captures_track_the_provider_contract_sources() -> None:
    required_assistant_sources = {
        "app/models/ai_models.py",
        "app/services/ai_agent_service.py",
        "app/services/ai_model_catalog_service.py",
    }
    required_settings_sources = {
        "app/models/ai_models.py",
        "app/services/ai_agent_service.py",
    }
    for config_path in (
        QA_CONFIG,
        PROJECT_ROOT / "qa" / "offscreen" / "guide_scenarios.json",
    ):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        scenarios = {item["id"]: item for item in config["scenarios"]}
        assert required_assistant_sources <= set(
            scenarios["guide_assistant_overview"]["source_paths"]
        )
        assert required_settings_sources <= set(
            scenarios["guide_settings_overview"]["source_paths"]
        )


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
    config = json.loads(QA_CONFIG.read_text(encoding="utf-8"))
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
    assert len(report.results) == len(config["scenarios"])
    assert len(report.layout_checks) == 21
    assert report.json_path.is_file()
    assert report.markdown_path.is_file()
    assert len(list(output_dir.glob("*.png"))) == len(config["scenarios"])

    guide_assets_dir = tmp_path / "guide-assets"
    capture_manifest_path = export_guide_assets(
        QA_CONFIG,
        report,
        guide_assets_dir,
    )
    capture_manifest = json.loads(capture_manifest_path.read_text(encoding="utf-8"))
    assert capture_manifest["schema_version"] == CAPTURE_MANIFEST_SCHEMA_VERSION
    assert capture_manifest["fingerprint_algorithm"] == "sha256"
    expected_assets = {
        scenario["guide_asset"]
        for scenario in config["scenarios"]
        if scenario.get("guide_asset")
    }
    assert {item["file"] for item in capture_manifest["assets"]} == expected_assets
    scenarios_by_id = {
        scenario["id"]: scenario
        for scenario in config["scenarios"]
        if scenario.get("guide_asset")
    }
    for item in capture_manifest["assets"]:
        scenario = scenarios_by_id[item["scenario_id"]]
        assert item["capture_handler"] == scenario["capture_handler"]
        assert item["viewport"] == scenario["viewport"]
        assert item["expected_object_names"] == scenario["expected_object_names"]
        assert item["source_paths"] == scenario["source_paths"]
        assert item["fingerprint"] == build_capture_fingerprint(
            PROJECT_ROOT,
            config,
            scenario,
        )
        assert len(item["png_sha256"]) == 64
        assert any("objectName 표시" in check for check in item["checks"])
    for asset_name in expected_assets:
        image = QImage(str(guide_assets_dir / asset_name))
        assert not image.isNull()
        assert image.width() == 1280
        assert image.height() == 800
    assert capture_manifest_errors(
        PROJECT_ROOT,
        QA_CONFIG,
        capture_manifest_path,
        assets_root=guide_assets_dir,
    ) == []

    stale_config = json.loads(QA_CONFIG.read_text(encoding="utf-8"))
    stale_scenario = next(
        item
        for item in stale_config["scenarios"]
        if item["id"] == "guide_interface_overview"
    )
    stale_scenario["viewport"] = [1279, 800]
    stale_scenario["expected_object_names"].append("newInterfaceContract")
    stale_config_path = tmp_path / "stale-guide-scenarios.json"
    stale_config_path.write_text(
        json.dumps(stale_config, ensure_ascii=False),
        encoding="utf-8",
    )
    stale_errors = capture_manifest_errors(
        PROJECT_ROOT,
        stale_config_path,
        capture_manifest_path,
        assets_root=guide_assets_dir,
    )
    assert any("recorded viewport is stale" in error for error in stale_errors)
    assert any("objectName expectations are stale" in error for error in stale_errors)
    assert any("fingerprint is stale" in error for error in stale_errors)
    assert any("do not match viewport" in error for error in stale_errors)


def test_guide_capture_fingerprint_tracks_handler_and_mapped_sources(tmp_path):
    handler_path = tmp_path / "qa" / "offscreen" / "harness.py"
    handler_path.parent.mkdir(parents=True)
    handler_path.write_text(
        """\
class OffscreenQaHarness:
    def _run_scenario(self):
        return None

    def _navigate_main(self):
        return None

    def _click_list_row(self):
        return None

    def _flush(self):
        return None

    def _capture(self):
        return None

    def _capture_handler_guide_overview(self):
        return 'first'
""",
        encoding="utf-8",
    )
    source_path = tmp_path / "app" / "page.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("PAGE_TITLE = 'first'\n", encoding="utf-8")
    config = {
        "schema_version": 1,
        "application": "NetOps Suite",
        "capture_delay_ms": 30,
    }
    scenario = {
        "id": "guide_page_overview",
        "title": "가이드 이미지",
        "viewport": [1280, 800],
        "guide_asset": "page-overview.png",
        "capture_handler": "guide_overview",
        "expected_object_names": ["appShell", "pageContent"],
        "source_paths": ["app/page.py"],
    }

    initial = build_capture_fingerprint(tmp_path, config, scenario)
    changed_contract = build_capture_fingerprint(
        tmp_path,
        config,
        {**scenario, "viewport": [1440, 900]},
    )
    assert changed_contract["contract_sha256"] != initial["contract_sha256"]
    assert changed_contract["value"] != initial["value"]

    source_path.write_text("PAGE_TITLE = 'second'\n", encoding="utf-8")
    changed_source = build_capture_fingerprint(tmp_path, config, scenario)
    assert changed_source["sources_sha256"] != initial["sources_sha256"]
    assert changed_source["value"] != initial["value"]

    handler_source = handler_path.read_text(encoding="utf-8")
    handler_path.write_text(
        handler_source.replace("return 'first'", "return 'second'"),
        encoding="utf-8",
    )
    changed_handler = build_capture_fingerprint(tmp_path, config, scenario)
    assert changed_handler["handler_sha256"] != initial["handler_sha256"]
    assert changed_handler["value"] != initial["value"]


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
    monkeypatch.setattr(
        "netops_suite.modules.config_builder.switch_configurator."
        "desktop_impl.APP_STATE_PATH",
        tmp_path / "config-builder" / ".desktop_state.json",
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
