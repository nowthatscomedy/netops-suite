from __future__ import annotations

from app.utils.file_utils import build_app_paths, load_json, save_json


def test_config_builder_desktop_data_dir_follows_runtime_data_root(monkeypatch, tmp_path):
    from netops_suite.modules.config_builder.switch_configurator import desktop_impl

    data_root = tmp_path / "runtime"
    monkeypatch.setenv("NETOPS_SUITE_DATA_ROOT", str(data_root))

    assert desktop_impl._default_config_builder_data_dir() == data_root / "config_builder"


def test_config_builder_state_read_paths_include_legacy_only_for_default(monkeypatch, tmp_path):
    from netops_suite.modules.config_builder.switch_configurator import desktop_impl

    default_state = tmp_path / "data" / ".desktop_state.json"
    legacy_state = tmp_path / "legacy" / ".desktop_state.json"
    custom_state = tmp_path / "custom_state.json"

    monkeypatch.setattr(desktop_impl, "DEFAULT_APP_STATE_PATH", default_state)
    monkeypatch.setattr(desktop_impl, "LEGACY_APP_STATE_PATH", legacy_state)
    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", default_state)
    assert desktop_impl._app_state_read_paths() == [default_state, legacy_state]

    monkeypatch.setattr(desktop_impl, "APP_STATE_PATH", custom_state)
    assert desktop_impl._app_state_read_paths() == [custom_state]


def test_build_app_paths_defaults_to_local_appdata(monkeypatch, tmp_path):
    local_appdata = tmp_path / "local_appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delenv("NETOPS_SUITE_DATA_ROOT", raising=False)
    monkeypatch.delenv("NETOPS_SUITE_USE_PROJECT_DATA", raising=False)

    paths = build_app_paths()

    assert paths.data_root == local_appdata / "NetOps Suite"


def test_build_app_paths_explicit_root_keeps_project_data(monkeypatch, tmp_path):
    monkeypatch.delenv("NETOPS_SUITE_DATA_ROOT", raising=False)

    paths = build_app_paths(tmp_path)

    assert paths.root == tmp_path
    assert paths.data_root == tmp_path


def test_load_json_backs_up_invalid_json(tmp_path):
    path = tmp_path / "app_config.json"
    path.write_text("{", encoding="utf-8")

    assert load_json(path, {"ok": True}) == {"ok": True}
    assert list(tmp_path.glob("app_config.json.invalid-*"))


def test_save_json_writes_valid_json_and_removes_temp_file(tmp_path):
    path = tmp_path / "config" / "app_config.json"

    save_json(path, {"name": "넷옵스"})

    assert load_json(path, {}) == {"name": "넷옵스"}
    assert not list(path.parent.glob(".app_config.json.*.tmp"))
