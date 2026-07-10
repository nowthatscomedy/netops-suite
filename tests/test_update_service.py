from __future__ import annotations

import hashlib
import logging

import pytest

from app.models.update_models import ReleaseAsset
from app.utils.file_utils import default_update_config, normalize_update_config
from app.services import update_service as update_service_module
from app.services.update_service import UpdateService


def test_default_update_config_is_stable_only():
    config = default_update_config()

    assert config["check_on_startup"] is False
    assert "github_repo" not in config
    assert "installer_asset_pattern" not in config
    assert "include_prerelease" not in config
    assert "release_channel" not in config


def test_normalize_update_config_drops_legacy_prerelease_options():
    normalized = normalize_update_config(
        {
            "check_on_startup": False,
            "github_repo": "someone/fork",
            "installer_asset_pattern": "custom.*\\.exe$",
            "include_prerelease": True,
            "release_channel": "prerelease",
        }
    )

    assert normalized == {
        **default_update_config(),
        "check_on_startup": False,
    }


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
    monkeypatch.setattr(service, "_fetch_release", lambda repo: release)

    result = service.check_for_updates("1.0.1", default_update_config())

    assert result.install_ready
    assert "무결성" in result.details
    assert "코드서명" in result.details


def test_update_service_requires_installer_version_to_match_release_tag(monkeypatch):
    service = UpdateService(logging.getLogger("test-update"))
    release = {
        "tag_name": "v1.0.8",
        "prerelease": False,
        "name": "NetOps Suite v1.0.8",
        "html_url": "https://example.test/release",
        "published_at": "2026-07-10T00:00:00Z",
        "body": "",
        "assets": [
            {
                "name": "NetOpsSuite-setup-9.9.9.exe",
                "browser_download_url": "https://example.test/wrong-installer",
                "size": 12,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }
    monkeypatch.setattr(service, "_fetch_release", lambda repo: release)

    result = service.check_for_updates("1.0.7", default_update_config())

    assert result.update_available is True
    assert result.install_ready is False
    assert result.asset is None
    assert "설치 파일을 찾지 못했습니다" in result.message


def test_update_service_ignores_prerelease_releases(monkeypatch):
    service = UpdateService(logging.getLogger("test-update"))
    stable_release = {
        "tag_name": "v1.0.2",
        "prerelease": False,
        "draft": False,
        "name": "NetOps Suite v1.0.2",
        "html_url": "https://example.test/stable",
        "published_at": "2026-05-10T00:00:00Z",
        "body": "",
        "assets": [
            {
                "name": "NetOpsSuite-setup-1.0.2.exe",
                "browser_download_url": "https://example.test/stable-installer",
                "size": 12,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }
    prerelease = {
        "tag_name": "v9.0.0-beta.1",
        "prerelease": True,
        "draft": False,
        "name": "NetOps Suite v9.0.0 beta",
        "html_url": "https://example.test/prerelease",
        "published_at": "2026-05-10T00:00:00Z",
        "body": "",
        "assets": [
            {
                "name": "NetOpsSuite-setup-9.0.0-beta.1.exe",
                "browser_download_url": "https://example.test/beta-installer",
                "size": 12,
                "digest": "sha256:" + "b" * 64,
            }
        ],
    }
    monkeypatch.setattr(service, "_request_json", lambda url: [prerelease, stable_release])

    result = service.check_for_updates(
        "1.0.1",
        {
            **default_update_config(),
            "include_prerelease": True,
            "release_channel": "prerelease",
        },
    )

    assert result.latest_version == "1.0.2"
    assert result.is_prerelease is False
    assert result.asset is not None
    assert result.asset.name == "NetOpsSuite-setup-1.0.2.exe"
