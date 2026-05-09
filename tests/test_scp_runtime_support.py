from __future__ import annotations

import logging
from pathlib import Path

import app.services.scp_client_service as scp_client_module
import app.services.scp_server_service as scp_server_module
from app.services.scp_client_service import ScpClientService
from app.services.scp_server_service import ScpServerService
from app.utils.file_utils import build_app_paths


def build_services(tmp_path: Path) -> tuple[ScpClientService, ScpServerService]:
    paths = build_app_paths(tmp_path)
    logger = logging.getLogger("scp-runtime-test")
    return ScpClientService(paths, logger), ScpServerService(paths, logger)


def test_scp_client_support_message_for_source_run_missing_paramiko(tmp_path, monkeypatch):
    client_service, _server_service = build_services(tmp_path)

    monkeypatch.setattr(scp_client_module, "paramiko", None)
    monkeypatch.setattr(scp_client_module, "is_packaged_runtime", lambda: False)
    monkeypatch.setattr(scp_client_module, "execution_environment_label", lambda: "소스 실행")

    result = client_service.runtime_support_status()

    assert result.success is False
    assert "소스 실행" in result.message
    assert "requirements.txt" in result.message
    assert "paramiko" in result.details
    assert "pip install -r requirements.txt" in result.details


def test_scp_server_support_message_for_packaged_run_missing_paramiko(tmp_path, monkeypatch):
    _client_service, server_service = build_services(tmp_path)

    monkeypatch.setattr(scp_server_module, "paramiko", None)
    monkeypatch.setattr(scp_server_module, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(scp_server_module, "execution_environment_label", lambda: "설치본 실행")

    result = server_service.runtime_support_status()

    assert result.success is False
    assert "설치본 실행" in result.message
    assert "paramiko" in result.details
    assert "최신 설치본" in result.details or "재설치" in result.details


def test_release_pipeline_includes_scp_profiles_without_runtime_state():
    project_root = Path(__file__).resolve().parents[1]
    build_script = (project_root / "scripts" / "build_release.ps1").read_text(encoding="utf-8")

    assert "scp_profiles.json" in build_script
    assert "scp_runtime.json" not in build_script
