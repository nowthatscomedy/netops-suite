from __future__ import annotations

import hashlib
import logging

import pytest

from app.models.update_models import ReleaseAsset
from app.utils.file_utils import default_update_config
from app.services import update_service as update_service_module
from app.services.update_service import UpdateService


def test_update_service_parses_sha256sums_for_named_asset(tmp_path):
    checksum_file = tmp_path / "SHA256SUMS.txt"
    expected = "a" * 64
    checksum_file.write_text(
        "\ufeff" + "\n".join(
            [
                "b" * 64 + " *other.exe",
                f"{expected} *NetOpsSuite-setup-1.0.1.exe",
            ]
        ),
        encoding="utf-8",
    )
    service = UpdateService(logging.getLogger("test-update"))

    assert service._parse_expected_sha256(checksum_file, "NetOpsSuite-setup-1.0.1.exe") == expected


def test_update_service_blocks_installer_if_file_changed_after_download(tmp_path):
    installer = tmp_path / "NetOpsSuite-setup-1.0.1.exe"
    original = b"verified installer"
    installer.write_bytes(original)
    expected_sha256 = hashlib.sha256(original).hexdigest()
    installer.write_bytes(b"tampered installer")
    service = UpdateService(logging.getLogger("test-update"))

    with pytest.raises(ValueError, match="재검증에 실패"):
        service.launch_installer(installer, expected_sha256=expected_sha256)


def test_update_service_rechecks_hash_before_launch(tmp_path, monkeypatch):
    installer = tmp_path / "NetOpsSuite-setup-1.0.1.exe"
    payload = b"verified installer"
    installer.write_bytes(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    captured: dict[str, object] = {}

    def fake_popen(args, cwd=None, creationflags=0):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["creationflags"] = creationflags

    monkeypatch.setattr(update_service_module.subprocess, "Popen", fake_popen)
    service = UpdateService(logging.getLogger("test-update"))

    service.launch_installer(installer, expected_sha256=expected_sha256)

    assert captured["args"] == [str(installer)]
    assert captured["cwd"] == str(tmp_path)


def test_update_service_selects_sha256sums_asset_for_installer():
    service = UpdateService(logging.getLogger("test-update"))
    installer = ReleaseAsset(name="NetOpsSuite-setup-1.0.1.exe", download_url="https://example.test/installer")
    sums = ReleaseAsset(name="sha256sums.TXT", download_url="https://example.test/sums")

    assert service._select_checksum_asset([installer, sums], installer.name) is sums


def test_update_service_labels_sha256_as_integrity_not_publisher_trust(monkeypatch):
    service = UpdateService(logging.getLogger("test-update"))
    release = {
        "tag_name": "v1.0.2",
        "prerelease": False,
        "name": "NetOps Suite v1.0.2",
        "html_url": "https://example.test/release",
        "published_at": "2026-05-10T00:00:00Z",
        "body": "",
        "assets": [
            {
                "name": "NetOpsSuite-setup-1.0.2.exe",
                "browser_download_url": "https://example.test/installer",
                "size": 12,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }
    monkeypatch.setattr(service, "_fetch_release", lambda repo, include_prerelease: release)

    result = service.check_for_updates("1.0.1", default_update_config())

    assert result.install_ready
    assert "무결성" in result.details
    assert "코드서명" in result.details
