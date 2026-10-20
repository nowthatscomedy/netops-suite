from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from pathlib import Path

import pytest

from app import portable_update
from app.models.update_models import DownloadedUpdate
from app.services.update_service import UpdateService
from app.utils import file_utils
from app.utils.file_utils import default_update_config

ROOT = Path(__file__).resolve().parents[1]


def _make_program(folder: Path, version: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "NetOpsSuite.exe").write_text(f"exe {version}", encoding="utf-8")
    (folder / "LICENSE").write_text(f"license {version}", encoding="utf-8")
    bundle = folder / "_internal"
    bundle.mkdir(exist_ok=True)
    (bundle / "core.dll").write_text(f"core {version}", encoding="utf-8")
    return folder


def _make_zip(zip_path: Path, payload: Path, top_level: str = "NetOpsSuite") -> None:
    with zipfile.ZipFile(zip_path, "w") as archive:
        for item in payload.rglob("*"):
            if item.is_file():
                archive.write(item, f"{top_level}/{item.relative_to(payload).as_posix()}")


def test_apply_replaces_program_files_and_keeps_data(tmp_path):
    target = _make_program(tmp_path / "usb" / "NetOpsSuite", "1.5.0")
    (target / "_internal" / "removed_in_new.dll").write_text("old", encoding="utf-8")
    (target / "data" / "config").mkdir(parents=True)
    (target / "data" / "config" / "app_config.json").write_text("{\"keep\": true}", encoding="utf-8")
    (target / "notes.txt").write_text("user file", encoding="utf-8")
    payload = _make_program(tmp_path / "staging" / "NetOpsSuite", "1.6.0")

    portable_update.apply_portable_update(payload, target)

    assert (target / "NetOpsSuite.exe").read_text(encoding="utf-8") == "exe 1.6.0"
    assert (target / "_internal" / "core.dll").read_text(encoding="utf-8") == "core 1.6.0"
    assert not (target / "_internal" / "removed_in_new.dll").exists()
    assert (target / "data" / "config" / "app_config.json").read_text(encoding="utf-8") == "{\"keep\": true}"
    assert (target / "notes.txt").read_text(encoding="utf-8") == "user file"
    assert not list(target.glob(f"{portable_update.BACKUP_PREFIX}*"))


def test_apply_restores_previous_version_when_copy_fails(tmp_path, monkeypatch):
    target = _make_program(tmp_path / "usb", "1.5.0")
    (target / "data").mkdir()
    payload = _make_program(tmp_path / "staging", "1.6.0")
    original_copy = portable_update._copy_item

    def failing_copy(source: Path, destination: Path) -> None:
        if source.name == "_internal":
            raise OSError("disk full")
        original_copy(source, destination)

    monkeypatch.setattr(portable_update, "_copy_item", failing_copy)

    with pytest.raises(portable_update.PortableUpdateError, match="되돌렸습니다"):
        portable_update.apply_portable_update(payload, target)

    assert (target / "NetOpsSuite.exe").read_text(encoding="utf-8") == "exe 1.5.0"
    assert (target / "_internal" / "core.dll").read_text(encoding="utf-8") == "core 1.5.0"
    assert (target / "LICENSE").read_text(encoding="utf-8") == "license 1.5.0"
    assert (target / "data").is_dir()
    assert not list(target.glob(f"{portable_update.BACKUP_PREFIX}*"))


def test_apply_rejects_payload_that_ships_a_data_folder(tmp_path):
    target = _make_program(tmp_path / "usb", "1.5.0")
    payload = _make_program(tmp_path / "staging", "1.6.0")
    (payload / "data").mkdir()

    with pytest.raises(portable_update.PortableUpdateError, match="data"):
        portable_update.apply_portable_update(payload, target)

    assert (target / "NetOpsSuite.exe").read_text(encoding="utf-8") == "exe 1.5.0"


def test_apply_refuses_folder_without_the_program(tmp_path):
    payload = _make_program(tmp_path / "staging", "1.6.0")
    target = tmp_path / "Documents"
    target.mkdir()
    (target / "report.docx").write_text("x", encoding="utf-8")

    with pytest.raises(portable_update.PortableUpdateError, match="교체할 폴더"):
        portable_update.apply_portable_update(payload, target)

    assert sorted(item.name for item in target.iterdir()) == ["report.docx"]


def test_safe_extract_blocks_paths_outside_destination(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../escape.txt", "x")

    with pytest.raises(portable_update.PortableUpdateError, match="허용되지 않는 경로"):
        portable_update.safe_extract_zip(zip_path, tmp_path / "out")

    assert not (tmp_path / "escape.txt").exists()


def test_result_file_is_read_once(tmp_path):
    result_file = tmp_path / "data" / portable_update.RESULT_FILENAME
    portable_update.write_result(result_file, success=False, version="1.6.0", message="잠김")

    first = portable_update.read_and_clear_result(result_file)

    assert first is not None
    assert first["success"] is False
    assert first["message"] == "잠김"
    assert portable_update.read_and_clear_result(result_file) is None


def test_apply_cli_from_source_run_reports_failure_without_touching_target(tmp_path):
    target = _make_program(tmp_path / "usb", "1.5.0")
    result_file = tmp_path / "result.json"

    exit_code = portable_update.run_apply_cli(
        ["--target", str(target), "--version", "1.6.0", "--result-file", str(result_file), "--no-relaunch"]
    )

    assert exit_code == 1
    assert json.loads(result_file.read_text(encoding="utf-8"))["success"] is False
    assert (target / "NetOpsSuite.exe").read_text(encoding="utf-8") == "exe 1.5.0"


def test_apply_cli_from_new_executable_swaps_files_and_records_success(tmp_path, monkeypatch):
    target = _make_program(tmp_path / "usb", "1.5.0")
    (target / "data").mkdir()
    payload = _make_program(tmp_path / "staging" / "NetOpsSuite", "1.6.0")
    result_file = target / "data" / portable_update.RESULT_FILENAME
    monkeypatch.setattr(portable_update.sys, "frozen", True, raising=False)
    monkeypatch.setattr(portable_update.sys, "executable", str(payload / "NetOpsSuite.exe"))

    exit_code = portable_update.run_apply_cli(
        ["--target", str(target), "--version", "1.6.0", "--result-file", str(result_file), "--no-relaunch"]
    )

    assert exit_code == 0
    assert (target / "NetOpsSuite.exe").read_text(encoding="utf-8") == "exe 1.6.0"
    result = portable_update.read_and_clear_result(result_file)
    assert result is not None and result["success"] is True


def test_main_dispatches_apply_flag_before_qt_import():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert source.index(portable_update.APPLY_FLAG) < source.index("from PySide6")


def test_portable_copy_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(file_utils, "is_packaged_runtime", lambda: True)
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.setenv(env_name, str(tmp_path / "Program Files"))
    usb_root = tmp_path / "usb" / "NetOpsSuite"
    usb_root.mkdir(parents=True)
    installed_root = tmp_path / "Program Files" / "NetOps Suite"
    installed_root.mkdir(parents=True)
    (installed_root / "unins000.exe").write_text("", encoding="utf-8")
    other_installed = tmp_path / "custom" / "NetOps Suite"
    other_installed.mkdir(parents=True)
    (other_installed / "unins000.exe").write_text("", encoding="utf-8")

    assert file_utils.is_portable_copy(usb_root) is True
    assert file_utils.is_portable_copy(installed_root) is False
    assert file_utils.is_portable_copy(other_installed) is False

    monkeypatch.setattr(file_utils, "is_packaged_runtime", lambda: False)
    assert file_utils.is_portable_copy(usb_root) is False


def _release_with_assets(version: str) -> dict:
    return {
        "tag_name": f"v{version}",
        "prerelease": False,
        "name": f"NetOps Suite v{version}",
        "html_url": "https://example.test/release",
        "published_at": "2026-10-20T00:00:00Z",
        "body": "",
        "assets": [
            {
                "name": f"NetOpsSuite-setup-{version}.exe",
                "browser_download_url": "https://example.test/installer",
                "size": 12,
                "digest": "sha256:" + "a" * 64,
            },
            {
                "name": f"NetOpsSuite-portable-{version}.zip",
                "browser_download_url": "https://example.test/portable",
                "size": 12,
                "digest": "sha256:" + "b" * 64,
            },
        ],
    }


def test_portable_check_selects_zip_asset(monkeypatch):
    service = UpdateService(logging.getLogger("test-update"))
    monkeypatch.setattr(service, "_fetch_release", lambda repo: _release_with_assets("1.6.0"))

    portable = service.check_for_updates("1.5.0", default_update_config(), portable=True)
    installer = service.check_for_updates("1.5.0", default_update_config())

    assert portable.install_ready is True
    assert portable.package_kind == "portable"
    assert portable.asset is not None
    assert portable.asset.name == "NetOpsSuite-portable-1.6.0.zip"
    assert "포터블 폴더" in portable.message
    assert installer.package_kind == "installer"
    assert installer.asset is not None
    assert installer.asset.name == "NetOpsSuite-setup-1.6.0.exe"


def test_portable_check_without_zip_asset_is_not_ready(monkeypatch):
    service = UpdateService(logging.getLogger("test-update"))
    release = _release_with_assets("1.6.0")
    release["assets"] = release["assets"][:1]
    monkeypatch.setattr(service, "_fetch_release", lambda repo: release)

    result = service.check_for_updates("1.5.0", default_update_config(), portable=True)

    assert result.update_available is True
    assert result.install_ready is False
    assert "포터블 zip 파일을 찾지 못했습니다" in result.message


def _downloaded_zip(tmp_path: Path) -> DownloadedUpdate:
    payload = _make_program(tmp_path / "build" / "NetOpsSuite", "1.6.0")
    download_dir = tmp_path / "netops_update_x"
    download_dir.mkdir()
    zip_path = download_dir / "NetOpsSuite-portable-1.6.0.zip"
    _make_zip(zip_path, payload)
    return DownloadedUpdate(
        version="1.6.0",
        asset_name=zip_path.name,
        asset_path=zip_path,
        checksum_path=None,
        sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        package_kind="portable",
    )


def test_prepare_portable_update_extracts_verified_zip(tmp_path):
    downloaded = _downloaded_zip(tmp_path)
    service = UpdateService(logging.getLogger("test-update"))

    payload_dir = service.prepare_portable_update(downloaded)

    assert payload_dir.name == "NetOpsSuite"
    assert (payload_dir / "NetOpsSuite.exe").read_text(encoding="utf-8") == "exe 1.6.0"
    assert (payload_dir / "_internal" / "core.dll").is_file()


def test_prepare_portable_update_blocks_changed_zip(tmp_path):
    downloaded = _downloaded_zip(tmp_path)
    with zipfile.ZipFile(downloaded.asset_path, "a") as archive:
        archive.writestr("NetOpsSuite/extra.txt", "tampered")
    service = UpdateService(logging.getLogger("test-update"))

    with pytest.raises(ValueError, match="재검증에 실패"):
        service.prepare_portable_update(downloaded)

    assert not (downloaded.asset_path.parent / "portable").exists()
