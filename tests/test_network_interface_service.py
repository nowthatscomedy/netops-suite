from __future__ import annotations

import logging
import subprocess

from app.models.result_models import CommandResult, OperationResult
from app.services.network_interface_service import NetworkInterfaceService


class FakePowerShell:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.scripts: list[str] = []

    @staticmethod
    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def run(self, script: str, timeout: int = 20) -> CommandResult:
        self.scripts.append(script)
        return self.result


def test_set_static_does_not_cleanup_after_failed_netsh_and_failed_fallback(monkeypatch):
    powershell = FakePowerShell(CommandResult("", "", "fallback failed", 1))
    service = NetworkInterfaceService(powershell, logging.getLogger("test"))
    cleanup_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        service,
        "_netsh_set_static",
        lambda *args: OperationResult(False, "netsh failed", "address failed"),
    )
    monkeypatch.setattr(
        service,
        "_cleanup_after_static",
        lambda *args: cleanup_calls.append(args) or OperationResult(True, "cleanup"),
    )

    result = service.set_static("Ethernet", "192.0.2.10", 24)

    assert not result.success
    assert cleanup_calls == []
    assert powershell.scripts


def test_static_fallback_reuses_existing_target_ip_and_manages_gateway(monkeypatch):
    powershell = FakePowerShell(CommandResult("", "", "", 0))
    service = NetworkInterfaceService(powershell, logging.getLogger("test"))

    monkeypatch.setattr(
        service,
        "_netsh_set_static",
        lambda *args: OperationResult(False, "netsh failed", "address failed"),
    )
    monkeypatch.setattr(
        service,
        "_cleanup_after_static",
        lambda *args: OperationResult(True, "cleanup"),
    )

    result = service.set_static("Ethernet", "192.0.2.10", 24, "192.0.2.1", ["8.8.8.8"])

    assert result.success
    script = powershell.scripts[-1]
    assert "$existingIp" in script
    assert "DefaultGateway" not in script
    assert "New-NetRoute" in script


def test_set_dhcp_renews_lease_after_netsh_success(monkeypatch):
    service = NetworkInterfaceService(FakePowerShell(CommandResult("", "", "", 0)), logging.getLogger("test"))
    calls: list[str] = []

    monkeypatch.setattr(service, "_netsh_interface_ref", lambda name: 'name="Ethernet"')
    monkeypatch.setattr(
        service,
        "_run_netsh",
        lambda command: calls.append(command[-1]) or OperationResult(True, "netsh"),
    )
    monkeypatch.setattr(service, "_cleanup_after_dhcp", lambda name: OperationResult(True, "cleanup", "cleaned"))
    monkeypatch.setattr(service, "_renew_dhcp_lease", lambda name: OperationResult(True, "renew", "lease renewed"))

    result = service.set_dhcp("Ethernet")

    assert result.success
    assert calls == ["source=dhcp", "source=dhcp"]
    assert "lease renewed" in result.details


def test_set_dhcp_reports_when_lease_is_not_available_yet(monkeypatch):
    service = NetworkInterfaceService(FakePowerShell(CommandResult("", "", "", 0)), logging.getLogger("test"))

    monkeypatch.setattr(service, "_netsh_interface_ref", lambda name: 'name="Ethernet"')
    monkeypatch.setattr(service, "_run_netsh", lambda command: OperationResult(True, "netsh"))
    monkeypatch.setattr(service, "_cleanup_after_dhcp", lambda name: OperationResult(True, "cleanup"))
    monkeypatch.setattr(
        service,
        "_renew_dhcp_lease",
        lambda name: OperationResult(
            True,
            "DHCP lease 미수신",
            "DHCP는 활성화됐지만 아직 정상 IPv4 lease를 받지 못했습니다.",
            {"lease_acquired": False},
        ),
    )

    result = service.set_dhcp("Ethernet")

    assert result.success
    assert "lease를 아직 받지 못했습니다" in result.message


def test_dhcp_cleanup_removes_default_route():
    powershell = FakePowerShell(CommandResult("", "", "", 0))
    service = NetworkInterfaceService(powershell, logging.getLogger("test"))

    result = service._cleanup_after_dhcp("Ethernet")

    assert result.success
    assert "Remove-NetRoute" in powershell.scripts[-1]


def test_netsh_timeout_returns_operation_result(monkeypatch):
    service = NetworkInterfaceService(FakePowerShell(CommandResult("", "", "", 0)), logging.getLogger("test"))

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=30, output="", stderr="timed out")

    monkeypatch.setattr("app.services.network_interface_service.subprocess.run", raise_timeout)

    result = service._run_netsh(["netsh"])

    assert not result.success
    assert "시간" in result.message
