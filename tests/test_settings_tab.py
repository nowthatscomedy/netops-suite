from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from app.ui.tabs.settings_tab import SettingsTab
from app.utils.file_utils import default_update_config, normalize_update_config


class DummySettingsState(QObject):
    config_reloaded = Signal()

    def __init__(self, tmp_path: Path) -> None:
        super().__init__()
        self.paths = SimpleNamespace(
            config_dir=tmp_path / "config",
            ip_profiles=tmp_path / "config" / "ip_profiles.json",
            logs_dir=tmp_path / "logs",
        )
        self.app_config = {"update": default_update_config()}
        self.saved_configs: list[dict] = []
        self.reload_count = 0

    def save_app_config(self, config: dict) -> None:
        normalized = dict(config)
        normalized["update"] = normalize_update_config(config.get("update", {}))
        self.app_config = normalized
        self.saved_configs.append(normalized)

    def reload_config_files(self) -> None:
        self.reload_count += 1
        self.config_reloaded.emit()


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


def test_settings_tab_path_labels_wrap_and_are_selectable(qapp, tmp_path):
    tab = SettingsTab(DummySettingsState(tmp_path))
    try:
        assert tab.open_config_button.text() == "설정 폴더 열기"
        assert tab.reload_button.text() == "다시 불러오기"
        for label in (tab.config_dir_label, tab.ip_profile_label, tab.log_dir_label):
            assert isinstance(label, QLabel)
            assert label.wordWrap()
            assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    finally:
        tab.close()
