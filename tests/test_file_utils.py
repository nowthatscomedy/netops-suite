from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest

from app.utils.file_utils import (
    build_app_paths,
    default_effective_path_settings,
    default_path_settings,
    effective_path_settings,
    ensure_runtime_files,
    load_json,
    migrate_config_directory,
    normalize_path_settings,
    resolve_app_paths_with_settings,
    save_json,
    timestamped_export_path,
    validate_path_settings,
)


def test_config_builder_desktop_data_dir_follows_runtime_data_root(
    monkeypatch, tmp_path
):
    from netops_suite.modules.config_builder.switch_configurator import desktop_impl

    data_root = tmp_path / "runtime"
    monkeypatch.setenv("NETOPS_SUITE_DATA_ROOT", str(data_root))

    assert (
        desktop_impl._default_config_builder_data_dir() == data_root / "config_builder"
    )


def test_config_builder_state_read_paths_include_legacy_only_for_default(
    monkeypatch, tmp_path
):
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


def test_save_json_concurrent_writes_use_distinct_temp_files(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "config" / "app_config.json"
    real_replace = os.replace
    start_barrier = Barrier(2)
    observed_sources: list[Path] = []
    replace_lock = Lock()
    active_replaces = 0
    max_active_replaces = 0

    def delayed_replace(source, target):
        nonlocal active_replaces, max_active_replaces
        source_path = Path(source)
        with replace_lock:
            observed_sources.append(source_path)
            active_replaces += 1
            max_active_replaces = max(max_active_replaces, active_replaces)
        try:
            time.sleep(0.05)
            return real_replace(source, target)
        finally:
            with replace_lock:
                active_replaces -= 1

    monkeypatch.setattr(os, "replace", delayed_replace)
    payloads = ({"writer": "first"}, {"writer": "second"})

    def write_payload(payload):
        start_barrier.wait(timeout=5)
        save_json(path, payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_payload, payload) for payload in payloads]
        for future in futures:
            future.result(timeout=5)

    assert len(set(observed_sources)) == 2
    assert max_active_replaces == 1
    assert load_json(path, {}) in payloads
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_timestamped_export_path_does_not_reuse_existing_file(
    monkeypatch,
    tmp_path,
):
    from app.utils import file_utils

    class FrozenDatetime:
        @classmethod
        def now(cls):
            return type(
                "FrozenValue", (), {"strftime": lambda self, _fmt: "20260717_120000"}
            )()

    monkeypatch.setattr(file_utils, "datetime", FrozenDatetime)
    first = timestamped_export_path(tmp_path, "diagnostic", "csv")
    first.write_text("first", encoding="utf-8")

    second = timestamped_export_path(tmp_path, "diagnostic", ".csv")

    assert second != first
    assert second.name == "diagnostic_20260717_120000_01.csv"
    assert first.read_text(encoding="utf-8") == "first"


def test_default_and_normalized_path_settings_have_stable_schema(tmp_path):
    assert default_path_settings() == {
        "version": 1,
        "config_dir": "",
        "logs_dir": "",
        "exports_dir": "",
    }
    assert normalize_path_settings(None) == default_path_settings()
    assert (
        normalize_path_settings({"version": 99, "config_dir": "ignored"})
        == default_path_settings()
    )
    assert normalize_path_settings(
        {
            "config_dir": tmp_path / "config",
            "logs_dir": "  C:/logs  ",
            "exports_dir": 123,
            "unknown": "ignored",
        }
    ) == {
        "version": 1,
        "config_dir": str(tmp_path / "config"),
        "logs_dir": "C:/logs",
        "exports_dir": "",
    }

    with_control = {"config_dir": f"{tmp_path / 'config'}\n"}
    normalized_with_control = normalize_path_settings(with_control)
    assert normalized_with_control["config_dir"].endswith("\n")
    with pytest.raises(ValueError, match="config_dir"):
        validate_path_settings(normalized_with_control)


def test_validate_path_settings_resolves_creates_and_probes_directories(tmp_path):
    config_dir = tmp_path / "custom" / "config"
    logs_dir = tmp_path / "custom" / "logs"

    validated = validate_path_settings(
        {
            "config_dir": str(config_dir),
            "logs_dir": logs_dir,
            "exports_dir": "",
        }
    )

    assert validated == {
        "version": 1,
        "config_dir": str(config_dir.resolve()),
        "logs_dir": str(logs_dir.resolve()),
        "exports_dir": "",
    }
    assert config_dir.is_dir()
    assert logs_dir.is_dir()
    assert not list(config_dir.glob(".netops_write_test_*"))
    assert not list(logs_dir.glob(".netops_write_test_*"))


def test_validate_path_settings_rejects_relative_control_file_and_unwritable_paths(
    monkeypatch,
    tmp_path,
):
    with pytest.raises(ValueError, match="config_dir"):
        validate_path_settings({"config_dir": "relative/config"})

    with pytest.raises(ValueError, match="config_dir"):
        validate_path_settings({"config_dir": f"{tmp_path / 'config'}\n"})

    file_path = tmp_path / "not-a-directory"
    file_path.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="config_dir"):
        validate_path_settings({"config_dir": str(file_path)})

    monkeypatch.setattr(
        "app.utils.file_utils._is_writable_directory", lambda _path: False
    )
    with pytest.raises(ValueError, match="config_dir"):
        validate_path_settings({"config_dir": str(tmp_path / "unwritable")})


def test_resolve_app_paths_applies_overrides_and_rebuilds_dependent_paths(tmp_path):
    base_paths = build_app_paths(tmp_path / "data")
    custom_config = tmp_path / "custom-config"
    custom_logs = tmp_path / "custom-logs"

    resolved = resolve_app_paths_with_settings(
        base_paths,
        {
            "config_dir": str(custom_config),
            "logs_dir": str(custom_logs),
            "exports_dir": "",
        },
    )

    assert resolved.config_dir == custom_config.resolve()
    assert resolved.logs_dir == custom_logs.resolve()
    assert resolved.exports_dir == custom_logs.resolve() / "exports"
    assert resolved.app_config == custom_config.resolve() / "app_config.json"
    assert (
        resolved.ai_model_catalog_cache
        == custom_config.resolve() / "ai_model_catalog_cache.json"
    )
    assert resolved.ftp_keys_dir == custom_config.resolve() / "ftp_keys"
    assert resolved.app_log == custom_logs.resolve() / "app.log"
    assert resolved.path_settings == base_paths.data_root / "path_settings.json"
    assert effective_path_settings(resolved) == {
        "version": 1,
        "config_dir": str(custom_config.resolve()),
        "logs_dir": str(custom_logs.resolve()),
        "exports_dir": str((custom_logs / "exports").resolve()),
    }
    assert default_effective_path_settings(resolved) == {
        "version": 1,
        "config_dir": str((base_paths.data_root / "config").resolve()),
        "logs_dir": str((base_paths.data_root / "logs").resolve()),
        "exports_dir": str((base_paths.data_root / "logs" / "exports").resolve()),
    }


def test_build_app_paths_loads_fixed_bootstrap_overrides(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    custom_config = tmp_path / "external" / "config"
    custom_logs = tmp_path / "external" / "logs"
    custom_exports = tmp_path / "external" / "exports"
    monkeypatch.setenv("NETOPS_SUITE_DATA_ROOT", str(data_root))
    save_json(
        data_root / "path_settings.json",
        {
            "version": 1,
            "config_dir": str(custom_config),
            "logs_dir": str(custom_logs),
            "exports_dir": str(custom_exports),
        },
    )

    paths = build_app_paths()

    assert paths.data_root == data_root
    assert paths.path_settings == data_root / "path_settings.json"
    assert paths.config_dir == custom_config.resolve()
    assert paths.logs_dir == custom_logs.resolve()
    assert paths.exports_dir == custom_exports.resolve()
    assert paths.app_config.parent == custom_config.resolve()
    assert paths.app_log.parent == custom_logs.resolve()


def test_build_app_paths_keeps_defaults_when_bootstrap_is_invalid(
    monkeypatch, tmp_path
):
    data_root = tmp_path / "data"
    monkeypatch.setenv("NETOPS_SUITE_DATA_ROOT", str(data_root))
    save_json(
        data_root / "path_settings.json",
        {"version": 1, "config_dir": "relative/config"},
    )

    paths = build_app_paths()

    assert paths.path_settings == data_root / "path_settings.json"
    assert paths.config_dir == data_root / "config"
    assert paths.logs_dir == data_root / "logs"
    assert paths.exports_dir == data_root / "logs" / "exports"


def test_build_app_paths_keeps_defaults_for_non_utf8_bootstrap(monkeypatch, tmp_path):
    data_root = tmp_path / "data"
    bootstrap = data_root / "path_settings.json"
    monkeypatch.setenv("NETOPS_SUITE_DATA_ROOT", str(data_root))
    data_root.mkdir()
    bootstrap.write_bytes(b"\xff\xfe\xfa")

    paths = build_app_paths()

    assert paths.config_dir == data_root / "config"
    assert list(data_root.glob("path_settings.json.invalid-*"))


def test_ensure_runtime_files_creates_fixed_bootstrap_with_defaults(tmp_path):
    paths = build_app_paths(tmp_path)

    ensure_runtime_files(paths)

    assert paths.path_settings == tmp_path / "path_settings.json"
    assert load_json(paths.path_settings, None) == default_path_settings()


def test_migrate_config_directory_copies_regular_files_without_overwriting(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "nested").mkdir(parents=True)
    target.mkdir()
    (source / "existing.json").write_text("source", encoding="utf-8")
    (source / "nested" / "new.json").write_text("new", encoding="utf-8")
    (target / "existing.json").write_text("target", encoding="utf-8")

    copied, skipped = migrate_config_directory(source, target)

    assert copied == ((target / "nested" / "new.json").resolve(),)
    assert skipped == ((source / "existing.json").resolve(),)
    assert (target / "existing.json").read_text(encoding="utf-8") == "target"
    assert (target / "nested" / "new.json").read_text(encoding="utf-8") == "new"


def test_migrate_config_directory_excludes_symbolic_links(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    real_file = source / "real.json"
    link_file = source / "link.json"
    real_file.write_text("real", encoding="utf-8")
    try:
        link_file.symlink_to(real_file)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are not available in this environment")

    copied, skipped = migrate_config_directory(source, target)

    assert copied == ((target / "real.json").resolve(),)
    assert skipped == (link_file.absolute(),)
    assert not (target / "link.json").exists()


def test_migrate_config_directory_rejects_source_directory_symbolic_link(tmp_path):
    source = tmp_path / "source"
    source_link = tmp_path / "source-link"
    source.mkdir()
    try:
        source_link.symlink_to(source, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are not available in this environment")

    with pytest.raises(ValueError):
        migrate_config_directory(source_link, tmp_path / "target")


def test_migrate_config_directory_rejects_symbolic_link_parent(tmp_path):
    real_parent = tmp_path / "real-parent"
    linked_parent = tmp_path / "linked-parent"
    source = real_parent / "source"
    source.mkdir(parents=True)
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are not available in this environment")

    with pytest.raises(ValueError):
        migrate_config_directory(linked_parent / "source", tmp_path / "target")
    with pytest.raises(ValueError):
        migrate_config_directory(source, linked_parent / "target")


def test_migrate_config_directory_skips_target_ancestor_file(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "nested").mkdir(parents=True)
    target.mkdir()
    source_file = source / "nested" / "config.json"
    source_file.write_text("source", encoding="utf-8")
    (target / "nested").write_text("existing file", encoding="utf-8")

    copied, skipped = migrate_config_directory(source, target)

    assert copied == ()
    assert skipped == (source_file.resolve(),)
    assert (target / "nested").read_text(encoding="utf-8") == "existing file"


def test_migrate_config_directory_does_not_follow_target_ancestor_symlink(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    (source / "nested").mkdir(parents=True)
    target.mkdir()
    outside.mkdir()
    source_file = source / "nested" / "config.json"
    source_file.write_text("source", encoding="utf-8")
    try:
        (target / "nested").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are not available in this environment")

    copied, skipped = migrate_config_directory(source, target)

    assert copied == ()
    assert skipped == (source_file.resolve(),)
    assert not (outside / "config.json").exists()


def test_migrate_config_directory_missing_or_same_source_is_noop(tmp_path):
    missing = tmp_path / "missing"
    source = tmp_path / "source"
    source.mkdir()

    assert migrate_config_directory(missing, tmp_path / "target") == ((), ())
    assert migrate_config_directory(source, source) == ((), ())


def test_migrate_config_directory_rejects_target_inside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(ValueError):
        migrate_config_directory(source, source / "nested-target")
