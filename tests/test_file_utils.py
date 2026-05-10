from __future__ import annotations

from app.utils.file_utils import build_app_paths, load_json, save_json


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
