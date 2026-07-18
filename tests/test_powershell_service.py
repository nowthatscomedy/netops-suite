from __future__ import annotations

import logging
import subprocess

from app.services.powershell_service import PowerShellService


def test_powershell_timeout_decodes_byte_streams(monkeypatch):
    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd="powershell.exe",
            timeout=3,
            output="부분 출력".encode(),
            stderr="시간 초과 오류".encode(),
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    service = PowerShellService(logging.getLogger("test.powershell"))

    result = service.run("Get-Date", timeout=3)

    assert result.timed_out is True
    assert result.returncode == -1
    assert result.stdout == "부분 출력"
    assert result.stderr == "시간 초과 오류"
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


def test_powershell_timeout_uses_fallback_when_stderr_is_empty(monkeypatch):
    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd="powershell.exe",
            timeout=7,
            output=b"",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    service = PowerShellService(logging.getLogger("test.powershell"))

    result = service.run("Get-Date", timeout=7)

    assert result.stdout == ""
    assert result.stderr == "PowerShell timed out after 7 seconds."
