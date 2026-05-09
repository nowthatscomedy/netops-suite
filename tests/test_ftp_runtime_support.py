from __future__ import annotations

import logging
from pathlib import Path

import app.services.ftp_client_service as ftp_client_module
import app.services.ftp_server_service as ftp_server_module
from app.services.ftp_client_service import FtpClientService
from app.services.ftp_server_service import FtpServerService
from app.utils.file_utils import build_app_paths


def build_services(tmp_path: Path) -> tuple[FtpClientService, FtpServerService]:
    paths = build_app_paths(tmp_path)
    logger = logging.getLogger("ftp-runtime-test")
    return FtpClientService(paths, logger), FtpServerService(paths, logger)


def test_ftp_client_support_message_for_source_run_missing_paramiko(tmp_path, monkeypatch):
    client_service, _server_service = build_services(tmp_path)

    monkeypatch.setattr(ftp_client_module, "paramiko", None)
    monkeypatch.setattr(ftp_client_module, "is_packaged_runtime", lambda: False)
    monkeypatch.setattr(ftp_client_module, "execution_environment_label", lambda: "소스 실행")

    result = client_service.runtime_support_status("sftp")

    assert result.success is False
    assert "소스 실행" in result.message
    assert "requirements.txt" in result.message
    assert "pip install -r requirements.txt" in result.details


def test_ftp_server_support_message_for_packaged_run_missing_dependency(tmp_path, monkeypatch):
    _client_service, server_service = build_services(tmp_path)

    monkeypatch.setattr(ftp_server_module, "DummyAuthorizer", None)
    monkeypatch.setattr(ftp_server_module, "ThreadedFTPServer", None)
    monkeypatch.setattr(ftp_server_module, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(ftp_server_module, "execution_environment_label", lambda: "설치본 실행")

    result = server_service.runtime_support_status("ftp")

    assert result.success is False
    assert "설치본 실행" in result.message
    assert "손상" in result.message or "손상" in result.details
    assert "최신 설치본" in result.details


def test_release_pipeline_checks_ftp_runtime_dependencies():
    project_root = Path(__file__).resolve().parents[1]
    build_script = (project_root / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    workflow = (project_root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "import pyftpdlib, OpenSSL, paramiko, tftpy" in build_script
    assert "--add-data=" in build_script
    assert "--add-binary=" in build_script
    assert ";$normalizedDestination" in build_script
    assert "ftp_profiles.json" in build_script
    assert "ftp_runtime.json" not in build_script
    assert "Verify File Transfer Runtime Dependencies" in workflow
    assert "import pyftpdlib, OpenSSL, paramiko, tftpy" in workflow
