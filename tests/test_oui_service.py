from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from app.models.network_models import OuiRecord
from app.services import oui_service as oui_service_module
from app.services.oui_service import OuiService
from app.utils.file_utils import build_app_paths, save_json


def _service(tmp_path: Path) -> OuiService:
    paths = build_app_paths(tmp_path)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    return OuiService(paths, logging.getLogger("test.oui"))


def _legacy_cache(service: OuiService) -> bytes:
    save_json(
        service.paths.oui_cache,
        {
            "updated_at": "2026-07-01 12:00:00",
            "records": [
                {
                    "prefix": "001122",
                    "prefix_bits": 24,
                    "organization": "Existing Vendor",
                    "registry": "MA-L",
                }
            ],
        },
    )
    return service.paths.oui_cache.read_bytes()


def _registry_response(
    registry: str,
    *,
    organization_suffix: str = "",
) -> tuple[list[OuiRecord], dict[str, object]]:
    registry_index = {
        "MA-L": 1,
        "MA-M": 2,
        "MA-S": 3,
        "CID": 4,
    }[registry]
    record = OuiRecord(
        prefix=f"{registry_index:06X}",
        prefix_bits=24,
        organization=f"{registry} Vendor{organization_suffix}",
        registry=registry,
    )
    return [record], {
        "registry": registry,
        "url": f"https://example.test/{registry}.csv",
        "record_count": 1,
        "sha256": f"source-{registry_index}",
        "etag": f'"etag-{registry_index}"',
        "last_modified": f"Wed, 0{registry_index} Jul 2026 00:00:00 GMT",
        "fetched_at": "2026-07-18T12:00:00+09:00",
    }


def _install_fake_registries(
    monkeypatch: pytest.MonkeyPatch,
    service: OuiService,
    *,
    changed_registry: str = "",
    failing_registry: str = "",
) -> None:
    def fetch(registry: str, _url: str):
        if registry == failing_registry:
            raise RuntimeError("network unavailable")
        suffix = " Changed" if registry == changed_registry else ""
        return _registry_response(registry, organization_suffix=suffix)

    monkeypatch.setattr(service, "_fetch_registry_payload", fetch)


def test_local_cache_status_never_uses_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _legacy_cache(service)
    monkeypatch.setattr(
        oui_service_module,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("local status must not use network"),
    )

    status = service.cache_status(
        now=datetime.fromisoformat("2026-07-18T12:00:00+09:00")
    )

    assert status["available"] is True
    assert status["record_count"] == 1
    assert status["dataset_version"] == ""
    assert status["version_label"] == ""
    assert status["source_name"] == "IEEE Registration Authority"
    assert status["age_days"] == 17
    assert status["stale"] is False
    assert service.lookup_vendor("00:11:22:AA:BB:CC") == "Existing Vendor"


def test_cache_status_marks_missing_or_old_data_stale(tmp_path: Path) -> None:
    service = _service(tmp_path)
    missing = service.cache_status(
        now=datetime.fromisoformat("2026-07-18T12:00:00+09:00")
    )
    assert missing["available"] is False
    assert missing["stale"] is True

    _legacy_cache(service)
    service._loaded = False
    old = service.cache_status(
        now=datetime.fromisoformat("2026-08-15T12:00:00+09:00")
    )
    assert old["age_days"] == 45
    assert old["stale"] is True


def test_refresh_replaces_cache_only_after_all_ieee_sources_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _legacy_cache(service)
    service.ensure_loaded()
    _install_fake_registries(monkeypatch, service)
    progress: list[str] = []

    result = service.refresh_cache(progress.append)

    assert result.success is True
    assert result.payload["count"] == 4
    assert result.payload["dataset_version"]
    assert result.payload["version_label"].startswith("SHA-256 ")
    assert len(progress) == 4
    assert service.lookup_vendor("00:00:01:AA:BB:CC") == "MA-L Vendor"

    payload = json.loads(service.paths.oui_cache.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["source_name"] == "IEEE Registration Authority"
    assert payload["dataset_version"] == service.dataset_version
    assert payload["source_updated_at"] == "Wed, 04 Jul 2026 00:00:00 GMT"
    assert set(payload["sources"]) == {"MA-L", "MA-M", "MA-S", "CID"}
    assert len(payload["records"]) == 4


def test_refresh_preserves_disk_and_memory_when_any_source_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    original_bytes = _legacy_cache(service)
    service.ensure_loaded()
    original_records = list(service.records)
    _install_fake_registries(
        monkeypatch,
        service,
        failing_registry="MA-M",
    )

    result = service.refresh_cache()

    assert result.success is False
    assert "기존 캐시" in result.details
    assert service.paths.oui_cache.read_bytes() == original_bytes
    assert service.records == original_records
    assert service.lookup_vendor("00:11:22:AA:BB:CC") == "Existing Vendor"


def test_refresh_preserves_memory_when_atomic_cache_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    original_bytes = _legacy_cache(service)
    service.ensure_loaded()
    original_records = list(service.records)
    _install_fake_registries(monkeypatch, service)

    def fail_save(_path: Path, _payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(oui_service_module, "save_json", fail_save)

    result = service.refresh_cache()

    assert result.success is False
    assert "기존 캐시" in result.details
    assert service.paths.oui_cache.read_bytes() == original_bytes
    assert service.records == original_records


def test_update_check_reports_latest_without_mutating_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _install_fake_registries(monkeypatch, service)
    assert service.refresh_cache().success is True
    original_bytes = service.paths.oui_cache.read_bytes()
    original_records = list(service.records)

    result = service.check_for_updates()

    assert result.success is True
    assert result.message == "OUI 데이터가 최신 상태입니다."
    assert result.payload["update_available"] is False
    assert result.payload["is_latest"] is True
    assert result.payload["remote"]["dataset_version"] == service.dataset_version
    assert service.paths.oui_cache.read_bytes() == original_bytes
    assert service.records == original_records
    assert service.last_checked_at


def test_update_check_reports_changed_authoritative_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    _install_fake_registries(monkeypatch, service)
    assert service.refresh_cache().success is True
    local_version = service.dataset_version
    original_bytes = service.paths.oui_cache.read_bytes()
    _install_fake_registries(
        monkeypatch,
        service,
        changed_registry="MA-S",
    )

    result = service.check_for_updates()

    assert result.success is True
    assert result.message == "최신 IEEE OUI 데이터가 있습니다."
    assert result.payload["update_available"] is True
    assert result.payload["is_latest"] is False
    assert result.payload["remote"]["dataset_version"] != local_version
    assert service.dataset_version == local_version
    assert service.paths.oui_cache.read_bytes() == original_bytes


def test_update_check_failure_keeps_local_cache_and_returns_unknown_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    original_bytes = _legacy_cache(service)
    service.ensure_loaded()
    _install_fake_registries(
        monkeypatch,
        service,
        failing_registry="CID",
    )

    result = service.check_for_updates()

    assert result.success is False
    assert result.payload["update_available"] is None
    assert result.payload["is_latest"] is None
    assert "현재 OUI 캐시는 변경하지 않았습니다." in result.details
    assert service.paths.oui_cache.read_bytes() == original_bytes
    assert service.lookup_vendor("00:11:22:AA:BB:CC") == "Existing Vendor"


def test_ieee_csv_download_captures_source_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    content = (
        "Registry,Assignment,Organization Name,Organization Address\n"
        "MA-L,001122,Example Networks,Seoul\n"
    ).encode("utf-8")

    class FakeResponse:
        headers = {
            "ETag": '"oui-test"',
            "Last-Modified": "Sat, 18 Jul 2026 00:00:00 GMT",
        }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return content

    monkeypatch.setattr(
        oui_service_module,
        "urlopen",
        lambda _request, timeout: FakeResponse() if timeout == 25 else None,
    )

    records, metadata = service._fetch_registry_payload(
        "MA-L",
        "https://standards-oui.ieee.org/oui/oui.csv",
    )

    assert records == [
        OuiRecord(
            prefix="001122",
            prefix_bits=24,
            organization="Example Networks",
            registry="MA-L",
        )
    ]
    assert metadata["record_count"] == 1
    assert metadata["etag"] == '"oui-test"'
    assert metadata["last_modified"] == "Sat, 18 Jul 2026 00:00:00 GMT"
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
    assert metadata["fetched_at"]
