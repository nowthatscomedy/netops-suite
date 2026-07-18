from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.ui.tabs.config_builder_tab import ConfigBuilderTab
from netops_suite.modules.config_builder.switch_configurator import desktop_impl
from netops_suite.modules.config_builder.switch_configurator.desktop_impl import (
    SwitchConfigBuilderWidget,
    normalize_user_save_path,
)
from netops_suite.modules.config_builder.switch_configurator.models import (
    RenderedConfig,
)


@pytest.fixture
def builder(qapp, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        desktop_impl,
        "APP_STATE_PATH",
        tmp_path / "desktop_state.json",
    )
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    widget = SwitchConfigBuilderWidget(
        profiles_dir=profiles_dir,
        exports_dir=tmp_path / "exports",
    )
    monkeypatch.setattr(widget, "_append_activity_log", lambda _message: None)
    yield widget
    widget.close()


def test_normalize_user_save_path_corrects_missing_and_unsupported_suffixes():
    assert normalize_user_save_path(
        "report",
        allowed_suffixes=(".txt",),
        default_suffix=".txt",
    ) == Path("report.txt")
    assert normalize_user_save_path(
        "report.md",
        allowed_suffixes=(".txt",),
        default_suffix=".txt",
    ) == Path("report.txt")
    assert normalize_user_save_path(
        "report.TXT",
        allowed_suffixes=(".txt",),
        default_suffix=".txt",
    ) == Path("report.TXT")
    assert (
        normalize_user_save_path(
            "",
            allowed_suffixes=(".txt",),
            default_suffix=".txt",
        )
        is None
    )


def test_device_file_save_prompts_in_exports_dir_and_applies_selected_extension(
    builder: SwitchConfigBuilderWidget,
    tmp_path: Path,
    monkeypatch,
):
    builder.current_file_path = tmp_path / "opened" / "devices.xlsx"
    chosen_without_suffix = tmp_path / "chosen" / "site_copy"
    captured: dict[str, str] = {}

    def choose_path(_parent, title, initial_path, file_filter):
        captured.update(
            title=title,
            initial_path=initial_path,
            file_filter=file_filter,
        )
        return str(chosen_without_suffix), "CSV Files (*.csv)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)

    saved_path = builder.save_current_file()

    assert captured["title"] == "장비 설정 정보 저장"
    assert Path(captured["initial_path"]) == builder.exports_dir / "devices.xlsx"
    assert "CSV Files (*.csv)" in captured["file_filter"]
    assert saved_path == chosen_without_suffix.with_suffix(".csv")
    assert saved_path.exists()
    assert builder.current_file_path == saved_path


def test_cancelled_device_file_save_creates_nothing(
    builder: SwitchConfigBuilderWidget,
    monkeypatch,
):
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )

    assert builder.save_current_file() is None
    assert not builder.exports_dir.exists()


def test_toolbar_save_action_opens_save_dialog_and_cancel_writes_nothing(
    builder: SwitchConfigBuilderWidget,
    monkeypatch,
):
    calls: list[str] = []

    def cancel_save(_parent, title, _initial_path, _file_filter):
        calls.append(title)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", cancel_save)
    save_actions = [
        action
        for action in builder.main_toolbar.actions()
        if action.text() == "장비 파일 저장"
    ]

    assert len(save_actions) == 1
    save_actions[0].trigger()
    QApplication.processEvents()

    assert calls == ["장비 설정 정보 저장"]
    assert not builder.exports_dir.exists()


def test_selected_cli_save_uses_exports_default_and_corrects_extension(
    builder: SwitchConfigBuilderWidget,
    tmp_path: Path,
    monkeypatch,
):
    builder.current_rendered = {
        0: RenderedConfig(
            device_id="SW01",
            profile_id="TEST",
            text="hostname SW01\n",
            values={},
            display_name="SW01",
        )
    }
    monkeypatch.setattr(builder, "_selected_source_rows", lambda: [0])
    chosen_without_suffix = tmp_path / "chosen" / "sw01_cli"
    captured: dict[str, str] = {}

    def choose_path(_parent, _title, initial_path, _file_filter):
        captured["initial_path"] = initial_path
        return str(chosen_without_suffix), "Text Files (*.txt)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)

    saved_path = builder.save_selected_cli()

    assert Path(captured["initial_path"]) == builder.exports_dir / "SW01.txt"
    assert saved_path == chosen_without_suffix.with_suffix(".txt")
    assert saved_path.read_text(encoding="utf-8") == "hostname SW01\n"


def test_all_cli_save_uses_selected_path_and_corrects_extension(
    builder: SwitchConfigBuilderWidget,
    tmp_path: Path,
    monkeypatch,
):
    builder.current_rendered = {
        0: RenderedConfig(
            device_id="SW01",
            profile_id="TEST",
            text="hostname SW01\n",
            values={},
            display_name="SW01",
        )
    }
    chosen_without_suffix = tmp_path / "chosen" / "all_cli"
    captured: dict[str, str] = {}

    def choose_path(_parent, title, initial_path, _file_filter):
        captured.update(title=title, initial_path=initial_path)
        return str(chosen_without_suffix), "Text Files (*.txt)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)

    saved_path = builder.save_all_cli()

    assert captured["title"] == "전체 CLI 저장"
    assert Path(captured["initial_path"]) == builder.exports_dir / "all_devices.txt"
    assert saved_path == chosen_without_suffix.with_suffix(".txt")
    assert "hostname SW01" in saved_path.read_text(encoding="utf-8")


def test_cancelled_all_cli_save_creates_nothing_and_shows_no_success(
    builder: SwitchConfigBuilderWidget,
    monkeypatch,
):
    builder.current_rendered = {
        0: RenderedConfig(
            device_id="SW01",
            profile_id="TEST",
            text="hostname SW01\n",
            values={},
            display_name="SW01",
        )
    }
    information_calls: list[tuple] = []
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: information_calls.append((args, kwargs)),
    )

    assert builder.save_all_cli() is None
    assert not builder.exports_dir.exists()
    assert information_calls == []


def test_each_cli_save_uses_single_zip_and_keeps_duplicate_names(
    builder: SwitchConfigBuilderWidget,
    tmp_path: Path,
    monkeypatch,
):
    builder.current_rendered = {
        0: RenderedConfig(
            device_id="SW01",
            profile_id="TEST",
            text="hostname SW01\n",
            values={},
            display_name="same/name",
        ),
        1: RenderedConfig(
            device_id="SW02",
            profile_id="TEST",
            text="hostname SW02\n",
            values={},
            display_name="same/name",
        ),
    }
    chosen_without_suffix = tmp_path / "chosen" / "per_device_cli"
    captured: dict[str, str] = {}

    def choose_path(_parent, title, initial_path, _file_filter):
        captured.update(title=title, initial_path=initial_path)
        return str(chosen_without_suffix), "ZIP Archives (*.zip)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)

    saved_path = builder.save_each_cli()

    assert captured["title"] == "장비별 CLI ZIP 저장"
    assert Path(captured["initial_path"]) == builder.exports_dir / "device_clis.zip"
    assert saved_path == chosen_without_suffix.with_suffix(".zip")
    with zipfile.ZipFile(saved_path) as archive:
        assert archive.namelist() == ["same_name.txt", "same_name_2.txt"]
        assert archive.read("same_name.txt").decode("utf-8") == "hostname SW01\n"
        assert archive.read("same_name_2.txt").decode("utf-8") == "hostname SW02\n"


def test_config_builder_tracks_runtime_exports_path_change(
    qapp,
    tmp_path: Path,
    monkeypatch,
):
    class FakeSignal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

        def emit(self):
            assert self.callback is not None
            self.callback()

    monkeypatch.setattr(
        desktop_impl,
        "APP_STATE_PATH",
        tmp_path / "desktop_state.json",
    )
    signal = FakeSignal()
    state = SimpleNamespace(
        paths=SimpleNamespace(
            data_root=tmp_path / "data",
            exports_dir=tmp_path / "exports-before",
        ),
        paths_changed=signal,
    )
    tab = ConfigBuilderTab(state)
    try:
        assert tab.builder_widget.exports_dir == tmp_path / "exports-before"

        state.paths.exports_dir = tmp_path / "exports-after"
        signal.emit()

        assert tab.builder_widget.exports_dir == tmp_path / "exports-after"
    finally:
        tab.close()
