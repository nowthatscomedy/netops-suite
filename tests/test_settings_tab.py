from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QPushButton

from app.app_state import AppState
from app.main_window import MainWindow
from app.ui.tabs.settings_tab import SettingsTab
from app.utils.file_utils import (
    build_app_paths,
    default_app_config,
    default_effective_path_settings,
    default_ftp_profiles,
    default_ftp_runtime,
    default_ip_profiles,
    default_scp_profiles,
    default_scp_runtime,
    default_tftp_runtime,
    default_update_config,
    ensure_runtime_files,
    load_json,
    normalize_update_config,
    resolve_app_paths_with_settings,
    save_json,
    validate_path_settings,
)


class DummySettingsState(QObject):
    config_reloaded = Signal()

    def __init__(self, tmp_path: Path) -> None:
        super().__init__()
        self.paths = build_app_paths(tmp_path)
        ensure_runtime_files(self.paths)
        self.app_config = {"update": default_update_config()}
        self.saved_configs: list[dict] = []
        self.saved_path_settings: list[dict[str, str]] = []
        self.reload_count = 0
        self.reset_count = 0

    def save_app_config(self, config: dict) -> None:
        normalized = dict(config)
        normalized["update"] = normalize_update_config(config.get("update", {}))
        self.app_config = normalized
        self.saved_configs.append(normalized)

    def save_path_settings(self, settings: dict) -> dict:
        validated = validate_path_settings(settings)
        target_paths = resolve_app_paths_with_settings(self.paths, validated)
        restart_required = any(
            getattr(target_paths, key) != getattr(self.paths, key)
            for key in ("config_dir", "logs_dir")
        )
        save_json(self.paths.path_settings, validated)
        self.paths.exports_dir = target_paths.exports_dir
        self.saved_path_settings.append(validated)
        return {
            "target_paths": target_paths,
            "copied_files": (),
            "skipped_files": (),
            "restart_required": restart_required,
        }

    def reload_config_files(self) -> None:
        self.reload_count += 1
        self.config_reloaded.emit()

    def reset_all_settings(self) -> dict[str, object]:
        self.reset_count += 1
        self.app_config = default_app_config()
        save_json(self.paths.app_config, self.app_config)
        self.config_reloaded.emit()
        return {"restart_required": True}


def test_settings_tab_update_controls_are_simplified(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        button_texts = {button.text() for button in tab.findChildren(QPushButton)}
        checkbox_texts = {checkbox.text() for checkbox in tab.findChildren(QCheckBox)}

        assert "업데이트 옵션 저장" not in button_texts
        assert "업데이트 확인" in button_texts
        assert "사전 배포(prerelease) 포함" not in checkbox_texts
        assert "프로그램 시작 시 업데이트 확인" in checkbox_texts
        assert not hasattr(tab, "save_update_button")
        assert not hasattr(tab, "include_prerelease_check")
    finally:
        tab.close()


def test_settings_tab_startup_update_toggle_saves_immediately(qapp, tmp_path):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    try:
        assert state.saved_configs == []

        tab.check_on_startup_check.setChecked(True)
        qapp.processEvents()

        assert state.saved_configs[-1]["update"] == {
            **default_update_config(),
            "check_on_startup": True,
        }
        assert "include_prerelease" not in state.saved_configs[-1]["update"]
        assert "release_channel" not in state.saved_configs[-1]["update"]
        assert "github_repo" not in state.saved_configs[-1]["update"]
        assert "installer_asset_pattern" not in state.saved_configs[-1]["update"]
    finally:
        tab.close()


def test_settings_tab_update_check_emits_stable_config_without_saving(qapp, tmp_path):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    captured: list[dict] = []
    tab.check_updates_requested.connect(lambda config: captured.append(config))
    try:
        tab.check_update_button.click()
        qapp.processEvents()

        assert captured == [default_update_config()]
        assert state.saved_configs == []
    finally:
        tab.close()


def test_settings_tab_path_controls_are_centralized_and_current_labels_are_selectable(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        assert set(tab.path_edits) == {"config_dir", "logs_dir", "exports_dir"}
        assert set(tab.path_open_buttons) == {"config_dir", "logs_dir", "exports_dir"}
        assert tab.reload_button.text() == "설정 파일 다시 불러오기"
        for label in (
            tab.config_dir_label,
            tab.ip_profile_label,
            tab.log_dir_label,
            tab.export_dir_label,
            tab.path_status_label,
        ):
            assert isinstance(label, QLabel)
            assert label.wordWrap()
            assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        assert "app_config.json" in tab.settings_files_view.toPlainText()
        assert "ai_model_catalog_cache.json" in tab.settings_files_view.toPlainText()
    finally:
        tab.close()


def test_settings_tab_uses_clear_internal_sections(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        assert [
            tab.section_tabs.tabText(index)
            for index in range(tab.section_tabs.count())
        ] == ["프로그램", "저장 위치", "도구 연동", "설정 관리"]
        assert tab.section_tabs.currentWidget() is tab.program_scroll

        tab.show_section("maintenance")
        assert tab.section_tabs.currentWidget() is tab.maintenance_scroll
        assert tab.reload_button.text() == "설정 파일 다시 불러오기"
        assert not hasattr(tab, "open_config_button")
        assert not hasattr(tab, "open_logs_button")
        assert not hasattr(tab, "open_exports_button")
    finally:
        tab.close()


def test_settings_tools_page_omits_redundant_built_in_tcping_explanation(
    qapp,
    tmp_path,
):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        group_titles = {
            group.title()
            for group in tab.tools_scroll.findChildren(QGroupBox)
        }
        labels = {
            label.text()
            for label in tab.tools_scroll.findChildren(QLabel)
        }

        assert "내장 도구" not in group_titles
        assert not any("별도 tcping 프로그램 없이" in text for text in labels)
    finally:
        tab.close()


def test_settings_tools_page_shows_shared_oui_version_and_source(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        tab._tools_loaded = True
        tab.show_section("tools", "oui")
        tab._apply_oui_status(
            {
                "available": True,
                "record_count": 42_123,
                "updated_at": "2026-06-01T12:00:00+09:00",
                "age_days": 47,
                "stale": True,
                "version_label": "SHA-256 abcdef123456",
                "source_name": "IEEE Registration Authority",
                "source_url": "https://standards-oui.ieee.org/",
                "source_updated_at": "Sat, 18 Jul 2026 00:00:00 GMT",
            }
        )

        assert tab.section_tabs.currentWidget() is tab.tools_scroll
        assert tab.oui_tool_group.title() == "OUI 제조사 데이터"
        assert "42,123건" in tab.oui_tool_status_label.text()
        assert "최신 여부 확인 권장" in tab.oui_tool_status_label.text()
        assert "SHA-256 abcdef123456" in tab.oui_tool_version_label.text()
        assert "IEEE Registration Authority" in tab.oui_tool_source_label.text()
        assert tab.oui_check_updates_button.text() == "최신 여부 확인"
        assert tab.oui_update_button.text() == "데이터 업데이트"
    finally:
        tab.close()


def test_settings_reset_requires_confirmation_and_cancel_changes_nothing(
    qapp,
    tmp_path,
    monkeypatch,
):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    confirmation_calls: list[dict] = []

    def reject_reset(*_args, **kwargs):
        confirmation_calls.append(kwargs)
        return False

    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.confirm_risky_action",
        reject_reset,
    )
    try:
        tab.reset_all_settings_button.click()
        qapp.processEvents()

        assert state.reset_count == 0
        assert confirmation_calls
        assert confirmation_calls[0]["confirm_text"] == "모든 설정 초기화"
        assert "자동으로 복구할 수 없습니다" in confirmation_calls[0]["reversibility"]
        assert "로그와 실행 결과" in confirmation_calls[0]["reversibility"]
    finally:
        tab.close()


def test_settings_reset_reports_preserved_outputs_and_restart(
    qapp,
    tmp_path,
    monkeypatch,
):
    state = DummySettingsState(tmp_path)
    state.app_config["custom"] = True
    tab = SettingsTab(state)
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.confirm_risky_action",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    try:
        tab.reset_all_settings_button.click()
        qapp.processEvents()

        assert state.reset_count == 1
        assert state.app_config == default_app_config()
        assert messages
        assert messages[-1][0] == "설정 초기화 완료"
        assert "로그" in messages[-1][1]
        assert "실행 결과·백업·내보낸 파일은 보존" in messages[-1][1]
        assert "다시 시작" in messages[-1][1]
        assert not tab.reset_all_settings_button.isEnabled()
        assert "다시 시작" in tab.reset_settings_status_label.text()
    finally:
        tab.close()


def test_app_state_reset_all_settings_resets_configuration_and_preserves_outputs(
    qapp,
    tmp_path,
    monkeypatch,
):
    test_logger = logging.getLogger(f"netops-reset-settings-test-{id(tmp_path)}")
    monkeypatch.setattr(
        "app.app_state.configure_logging",
        lambda *_args, **_kwargs: test_logger,
    )
    custom_config = tmp_path / "custom" / "config"
    custom_logs = tmp_path / "custom" / "logs"
    custom_exports = tmp_path / "custom" / "exports"
    first_state = AppState(tmp_path)
    first_state.save_path_settings(
        {
            "config_dir": str(custom_config),
            "logs_dir": str(custom_logs),
            "exports_dir": str(custom_exports),
        }
    )
    first_state.shutdown()

    state = AppState(tmp_path)
    log_sentinel = custom_logs / "application-history.log"
    export_sentinel = custom_exports / "saved-result.csv"
    inspector_run = tmp_path / "inspector" / "runs" / "preserved-result.xlsx"
    custom_rules = tmp_path / "inspector" / "custom_rules.yaml"
    custom_parser = tmp_path / "inspector" / "custom_parsers" / "custom_parser.py"
    for path, content in (
        (log_sentinel, "keep log"),
        (export_sentinel, "keep export"),
        (inspector_run, "keep run"),
        (custom_rules, "inspection_commands: {}"),
        (custom_parser, "def parse(_value): return {}"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    save_json(state.paths.app_config, {"custom": True, "ui_state": {"tab": 3}})
    save_json(state.paths.ip_profiles, [{"name": "custom profile", "mode": "dhcp"}])
    save_json(state.paths.ftp_profiles, [{"name": "custom ftp"}])
    save_json(state.paths.ftp_runtime, {"custom": True})
    save_json(state.paths.scp_profiles, [{"name": "custom scp"}])
    save_json(state.paths.scp_runtime, {"custom": True})
    save_json(state.paths.tftp_runtime, {"custom": True})
    state.reload_config_files()
    try:
        result = state.reset_all_settings()
        defaults = default_effective_path_settings(state.paths)

        assert result["restart_required"] is True
        assert state.settings_reset_pending_restart is True
        assert state.app_config == default_app_config()
        assert load_json(state.paths.app_config, {}) == default_app_config()
        assert load_json(state.paths.ip_profiles, []) == default_ip_profiles()
        assert load_json(state.paths.ftp_profiles, []) == default_ftp_profiles()
        assert load_json(state.paths.ftp_runtime, {}) == default_ftp_runtime()
        assert load_json(state.paths.scp_profiles, []) == default_scp_profiles()
        assert load_json(state.paths.scp_runtime, {}) == default_scp_runtime()
        assert load_json(state.paths.tftp_runtime, {}) == default_tftp_runtime()
        assert load_json(state.paths.path_settings, {}) == defaults
        assert state.paths.config_dir == Path(defaults["config_dir"])
        assert state.paths.exports_dir == Path(defaults["exports_dir"])
        assert state.paths.logs_dir == custom_logs.resolve()

        assert log_sentinel.read_text(encoding="utf-8") == "keep log"
        assert export_sentinel.read_text(encoding="utf-8") == "keep export"
        assert inspector_run.read_text(encoding="utf-8") == "keep run"
        assert not custom_rules.exists()
        assert not custom_parser.exists()
    finally:
        state.shutdown()


def test_main_window_skips_old_ui_state_save_after_settings_reset():
    window = SimpleNamespace(
        state=SimpleNamespace(settings_reset_pending_restart=True)
    )

    MainWindow._save_ui_state(window)


def test_settings_program_page_does_not_duplicate_diagnostic_defaults(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        group_titles = {
            group.title()
            for group in tab.program_scroll.findChildren(QGroupBox)
        }
        program_labels = {label.text() for label in tab.program_scroll.findChildren(QLabel)}

        assert "진단 기본값" not in group_titles
        assert "Ping 기본 횟수" not in program_labels
        assert "Ping 제한 시간 (ms)" not in program_labels
        assert "TCP 연결 제한 시간 (ms)" not in program_labels
        assert not hasattr(tab, "program_value_spins")
        assert not hasattr(tab, "save_program_button")
        assert {
            "default_ping_count",
            "default_ping_timeout_ms",
            "default_tcp_timeout_ms",
        }.isdisjoint(default_app_config())
    finally:
        tab.close()


def test_app_state_removes_retired_diagnostic_default_keys(
    qapp,
    tmp_path,
    monkeypatch,
):
    paths = build_app_paths(tmp_path)
    ensure_runtime_files(paths)
    stale_config = load_json(paths.app_config, {})
    stale_config.update(
        {
            "default_ping_count": 99,
            "default_ping_timeout_ms": 9999,
            "default_tcp_timeout_ms": 9999,
        }
    )
    save_json(paths.app_config, stale_config)
    test_logger = logging.getLogger(f"netops-retired-settings-test-{id(tmp_path)}")
    monkeypatch.setattr("app.app_state.configure_logging", lambda *_args, **_kwargs: test_logger)

    state = AppState(tmp_path)
    retired_keys = {
        "default_ping_count",
        "default_ping_timeout_ms",
        "default_tcp_timeout_ms",
    }
    try:
        assert retired_keys.isdisjoint(state.app_config)
        assert retired_keys.isdisjoint(load_json(state.paths.app_config, {}))

        state.save_app_config(
            {
                **state.app_config,
                "default_ping_count": 77,
                "default_ping_timeout_ms": 7777,
                "default_tcp_timeout_ms": 7777,
            }
        )
        assert retired_keys.isdisjoint(state.app_config)
        assert retired_keys.isdisjoint(load_json(state.paths.app_config, {}))
    finally:
        state.shutdown()


def test_settings_tab_saves_ai_cli_paths(qapp, tmp_path):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    changed: list[str] = []
    tab.integration_changed.connect(changed.append)
    try:
        custom_codex = tmp_path / "tools" / "codex.exe"
        tab.ai_cli_path_edits["codex"].setText(str(custom_codex))
        tab.save_ai_cli_paths_button.click()

        assert state.app_config["ai_chat"]["providers"]["codex"]["command_path"] == str(custom_codex)
        assert changed == ["ai"]
    finally:
        tab.close()


def test_settings_tab_saves_paths_and_applies_exports_immediately(qapp, tmp_path):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    custom_config = tmp_path / "custom" / "config"
    custom_logs = tmp_path / "custom" / "logs"
    custom_exports = tmp_path / "custom" / "exports"
    try:
        tab.config_dir_edit.setText(str(custom_config))
        tab.logs_dir_edit.setText(str(custom_logs))
        tab.exports_dir_edit.setText(str(custom_exports))
        tab.save_paths_button.click()
        qapp.processEvents()

        assert state.saved_path_settings[-1]["config_dir"] == str(custom_config.resolve())
        assert state.saved_path_settings[-1]["logs_dir"] == str(custom_logs.resolve())
        assert state.saved_path_settings[-1]["exports_dir"] == str(custom_exports.resolve())
        assert state.paths.exports_dir == custom_exports.resolve()
        assert "프로그램을 다시 시작" in tab.path_status_label.text()
        assert custom_config.is_dir()
        assert custom_logs.is_dir()
        assert custom_exports.is_dir()
    finally:
        tab.close()


def test_app_state_applies_relocated_config_immediately_and_preserves_later_saves(
    qapp,
    tmp_path,
    monkeypatch,
):
    test_logger = logging.getLogger(f"netops-path-settings-test-{id(tmp_path)}")
    monkeypatch.setattr("app.app_state.configure_logging", lambda *_args, **_kwargs: test_logger)
    state = AppState(tmp_path)
    original_logs_dir = state.paths.logs_dir
    original_app_config = state.paths.app_config
    custom_config = tmp_path / "relocated" / "config"
    custom_logs = tmp_path / "relocated" / "logs"
    custom_exports = tmp_path / "relocated" / "exports"
    try:
        result = state.save_path_settings(
            {
                "config_dir": str(custom_config),
                "logs_dir": str(custom_logs),
                "exports_dir": str(custom_exports),
            }
        )

        assert result["restart_required"] is True
        assert result["target_paths"].config_dir == custom_config.resolve()
        assert result["target_paths"].logs_dir == custom_logs.resolve()
        assert result["target_paths"].exports_dir == custom_exports.resolve()
        assert custom_config.joinpath("app_config.json").is_file()
        assert original_app_config.is_file()
        assert state.paths.config_dir == custom_config.resolve()
        assert state.paths.logs_dir == original_logs_dir
        assert state.paths.exports_dir == custom_exports.resolve()
        assert state.paths.app_config == custom_config.resolve() / original_app_config.name
        assert load_json(state.paths.path_settings, {})["config_dir"] == str(custom_config.resolve())

        updated_config = dict(state.app_config)
        updated_config["wireless_refresh_interval_sec"] = 17
        state.save_app_config(updated_config)
        assert load_json(custom_config / original_app_config.name, {})[
            "wireless_refresh_interval_sec"
        ] == 17

        restarted_paths = build_app_paths(tmp_path)
        assert restarted_paths.config_dir == custom_config.resolve()
        assert restarted_paths.logs_dir == custom_logs.resolve()
        assert restarted_paths.exports_dir == custom_exports.resolve()
    finally:
        state.shutdown()


def test_settings_tab_change_reset_and_invalid_path_feedback(qapp, tmp_path, monkeypatch):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    selected = tmp_path / "selected exports"
    selected.mkdir()
    warnings: list[str] = []
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.QFileDialog.getExistingDirectory",
        lambda *_args, **_kwargs: str(selected),
    )
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    try:
        tab.path_change_buttons["exports_dir"].click()
        assert tab.exports_dir_edit.text() == str(selected)

        tab.reset_paths_button.click()
        defaults = default_effective_path_settings(state.paths)
        assert tab._current_path_values() == {
            key: defaults[key]
            for key in ("config_dir", "logs_dir", "exports_dir")
        }

        tab.config_dir_edit.setText("relative/config")
        tab.save_paths_button.click()
        qapp.processEvents()
        assert warnings
        assert state.saved_path_settings == []
    finally:
        tab.close()


def test_settings_storage_change_cancel_preserves_input_and_dirty_state(
    qapp,
    tmp_path,
    monkeypatch,
):
    tab = SettingsTab(DummySettingsState(tmp_path))
    unsaved = str(tmp_path / "unsaved-exports")
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.QFileDialog.getExistingDirectory",
        lambda *_args, **_kwargs: "",
    )
    try:
        tab.exports_dir_edit.setText(unsaved)
        qapp.processEvents()
        status_before = tab.path_status_label.text()

        tab.path_change_buttons["exports_dir"].click()

        assert tab.exports_dir_edit.text() == unsaved
        assert tab._path_dirty
        assert tab.save_paths_button.isEnabled()
        assert tab.path_status_label.text() == status_before
    finally:
        tab.close()


def test_settings_storage_rows_open_current_displayed_folder(
    qapp,
    tmp_path,
    monkeypatch,
):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    edited_exports = tmp_path / "edited-exports"
    edited_exports.mkdir()
    opened: list[Path] = []
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.open_in_explorer",
        lambda path: opened.append(Path(path)),
    )
    try:
        tab.exports_dir_edit.setText(str(edited_exports))
        tab.path_open_buttons["exports_dir"].click()
        assert opened[-1] == edited_exports
    finally:
        tab.close()


def test_settings_storage_open_warns_for_missing_or_file_path(
    qapp,
    tmp_path,
    monkeypatch,
):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    warnings: list[tuple[str, str]] = []
    opened: list[Path] = []
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.open_in_explorer",
        lambda path: opened.append(Path(path)),
    )
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    try:
        invalid_paths = [tmp_path / "missing-edited-logs", tmp_path / "not-a-folder.txt"]
        invalid_paths[1].write_text("not a directory", encoding="utf-8")
        for invalid_path in invalid_paths:
            tab.logs_dir_edit.setText(str(invalid_path))
            tab.path_open_buttons["logs_dir"].click()

        assert opened == []
        assert len(warnings) == 2
        assert warnings[-1][0] == "폴더를 열 수 없음"
        assert "유효한 폴더로 변경" in warnings[-1][1]
    finally:
        tab.close()


def test_settings_storage_open_reports_explorer_failure(
    qapp,
    tmp_path,
    monkeypatch,
):
    tab = SettingsTab(DummySettingsState(tmp_path))
    target = tmp_path / "open-target"
    target.mkdir()
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.open_in_explorer",
        lambda _path: (_ for _ in ()).throw(OSError("explorer unavailable")),
    )
    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    try:
        tab.config_dir_edit.setText(str(target))
        tab.path_open_buttons["config_dir"].click()

        assert warnings == [
            (
                "폴더 열기 실패",
                "설정 파일 폴더를 열지 못했습니다: explorer unavailable",
            )
        ]
    finally:
        tab.close()


@pytest.mark.parametrize("width,height", [(816, 620), (1072, 740)])
def test_settings_tab_remains_scrollable_at_supported_window_sizes(qapp, tmp_path, width, height):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        tab.show_section("storage")
        tab.resize(width, height)
        tab.show()
        qapp.processEvents()

        assert tab.settings_scroll.widgetResizable()
        assert tab.settings_scroll.viewport().width() > 0
        assert tab.settings_scroll.verticalScrollBar().maximum() >= 0
        assert tab.settings_scroll.horizontalScrollBar().maximum() == 0
        assert tab.settings_files_view.width() > 0
        assert tab.save_paths_button.isVisible()
        assert all(button.isVisible() for button in tab.path_change_buttons.values())
        assert all(button.isVisible() for button in tab.path_open_buttons.values())
    finally:
        tab.close()


@pytest.mark.parametrize("width,height", [(816, 620), (1072, 740)])
def test_settings_management_reset_is_reachable_at_supported_window_sizes(
    qapp,
    tmp_path,
    width,
    height,
):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        tab.show_section("maintenance")
        tab.resize(width, height)
        tab.show()
        tab.maintenance_scroll.ensureWidgetVisible(tab.reset_all_settings_button)
        qapp.processEvents()

        assert tab.section_tabs.tabText(tab.section_tabs.currentIndex()) == "설정 관리"
        assert tab.reset_all_settings_button.isVisible()
        assert tab.maintenance_scroll.horizontalScrollBar().maximum() == 0
    finally:
        tab.close()


def test_settings_path_and_ai_cli_controls_have_contextual_accessible_names(
    qapp,
    tmp_path,
):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        path_labels = {
            "config_dir": "설정 파일 폴더",
            "logs_dir": "로그 폴더",
            "exports_dir": "결과/내보내기 폴더",
        }
        for key, label in path_labels.items():
            assert tab.path_edits[key].accessibleName() == label
            assert tab.path_edits[key].accessibleDescription()
            assert tab.path_change_buttons[key].text() == "변경"
            assert tab.path_change_buttons[key].accessibleName() == f"{label} 위치 변경"
            assert tab.path_change_buttons[key].toolTip() == f"새 {label} 위치를 선택합니다."
            assert tab.path_open_buttons[key].text() == "폴더 열기"
            assert tab.path_open_buttons[key].accessibleName() == f"{label} 폴더 열기"
            assert (
                tab.path_open_buttons[key].toolTip()
                == f"입력된 {label}를 파일 탐색기에서 엽니다."
            )

        provider_labels = {
            "codex": "ChatGPT Codex",
            "claude": "Claude Code",
            "gemini": "Gemini CLI",
        }
        for key, label in provider_labels.items():
            assert tab.ai_cli_path_edits[key].accessibleName() == f"{label} 실행 파일"
            assert tab.ai_cli_path_edits[key].accessibleDescription()
            assert (
                tab.ai_cli_browse_buttons[key].accessibleName()
                == f"{label} 실행 파일 찾아보기"
            )
            assert label in tab.ai_cli_browse_buttons[key].toolTip()
            assert tab.ai_cli_status_labels[key].accessibleName() == f"{label} 감지 상태"
    finally:
        tab.close()


def test_settings_path_actions_follow_dirty_and_default_state(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        assert not tab.save_paths_button.isEnabled()
        assert not tab.reset_paths_button.isEnabled()

        original = tab.config_dir_edit.text()
        tab.config_dir_edit.setText(str(tmp_path / "custom-config"))
        qapp.processEvents()

        assert tab.save_paths_button.isEnabled()
        assert tab.reset_paths_button.isEnabled()
        assert "저장되지 않은" in tab.path_status_label.text()

        tab.config_dir_edit.setText(original)
        qapp.processEvents()

        assert not tab.save_paths_button.isEnabled()
        assert not tab.reset_paths_button.isEnabled()
    finally:
        tab.close()


def test_settings_reload_confirms_before_discarding_dirty_paths(
    qapp,
    tmp_path,
    monkeypatch,
):
    state = DummySettingsState(tmp_path)
    tab = SettingsTab(state)
    confirmations: list[bool] = []

    def reject_reload(*_args, **_kwargs):
        confirmations.append(False)
        return False

    monkeypatch.setattr(
        "app.ui.tabs.settings_tab.confirm_risky_action",
        reject_reload,
    )
    try:
        original = tab.config_dir_edit.text()
        unsaved = str(tmp_path / "unsaved-config")
        tab.config_dir_edit.setText(unsaved)
        qapp.processEvents()

        tab.reload_button.click()
        qapp.processEvents()

        assert confirmations == [False]
        assert state.reload_count == 0
        assert tab._path_dirty
        assert tab.config_dir_edit.text() == unsaved

        monkeypatch.setattr(
            "app.ui.tabs.settings_tab.confirm_risky_action",
            lambda *_args, **_kwargs: True,
        )
        tab.reload_button.click()
        qapp.processEvents()

        assert state.reload_count == 1
        assert not tab._path_dirty
        assert tab.config_dir_edit.text() == original
        assert not tab.save_paths_button.isEnabled()
        assert "다시 불러왔습니다" in tab.maintenance_status_label.text()
        assert "변경을 버렸습니다" in tab.maintenance_status_label.text()
    finally:
        tab.close()
