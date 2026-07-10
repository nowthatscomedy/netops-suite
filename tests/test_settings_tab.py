from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from app.app_state import AppState
from app.ui.tabs.settings_tab import SettingsTab
from app.utils.file_utils import (
    build_app_paths,
    default_effective_path_settings,
    default_update_config,
    effective_path_settings,
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


def test_settings_tab_update_controls_are_simplified(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        button_texts = {button.text() for button in tab.findChildren(QPushButton)}
        checkbox_texts = {checkbox.text() for checkbox in tab.findChildren(QCheckBox)}

        assert "업데이트 옵션 저장" not in button_texts
        assert "확인" in button_texts
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
        assert tab.open_config_button.text() == "설정 폴더"
        assert tab.open_exports_button.text() == "결과 폴더"
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
    original_config_dir = state.paths.config_dir
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
        updated_config["default_ping_count"] = 17
        state.save_app_config(updated_config)
        assert load_json(custom_config / original_app_config.name, {})["default_ping_count"] == 17

        restarted_paths = build_app_paths(tmp_path)
        assert restarted_paths.config_dir == custom_config.resolve()
        assert restarted_paths.logs_dir == custom_logs.resolve()
        assert restarted_paths.exports_dir == custom_exports.resolve()
    finally:
        state.shutdown()


def test_settings_tab_browse_reset_and_invalid_path_feedback(qapp, tmp_path, monkeypatch):
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
        tab.path_browse_buttons["exports_dir"].click()
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


@pytest.mark.parametrize("width,height", [(816, 620), (1072, 740)])
def test_settings_tab_remains_scrollable_at_supported_window_sizes(qapp, tmp_path, width, height):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        tab.resize(width, height)
        tab.show()
        qapp.processEvents()

        assert tab.settings_scroll.widgetResizable()
        assert tab.settings_scroll.viewport().width() > 0
        assert tab.settings_scroll.verticalScrollBar().maximum() >= 0
        assert tab.settings_files_view.width() > 0
        assert tab.save_paths_button.isVisible()
    finally:
        tab.close()
