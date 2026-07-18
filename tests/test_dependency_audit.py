from __future__ import annotations

from datetime import date

from scripts import audit_dependencies


def test_dependency_audit_exception_is_narrow_and_short_lived():
    assert audit_dependencies.PARAMIKO_EXCEPTION_IDS == (
        "GHSA-r374-rxx8-8654",
        "CVE-2026-44405",
    )
    assert date(2026, 7, 18) < audit_dependencies.PARAMIKO_EXCEPTION_EXPIRES
    assert audit_dependencies.PARAMIKO_EXCEPTION_EXPIRES <= date(2026, 8, 31)


def test_dependency_audit_command_uses_current_environment_and_only_known_exception():
    command = audit_dependencies.build_pip_audit_command(["--format", "json"])

    assert command[:3] == [
        audit_dependencies.sys.executable,
        "-m",
        "pip_audit",
    ]
    assert "--local" in command
    assert command.count("--ignore-vuln") == 2
    assert command[-2:] == ["--format", "json"]


def test_dependency_sbom_command_uses_runtime_lock_without_build_environment():
    lock_path = audit_dependencies.Path("requirements-lock.txt")
    command = audit_dependencies.build_pip_audit_command(
        ["--format", "cyclonedx-json"],
        requirements=lock_path,
    )

    assert "--local" not in command
    assert command[3:6] == [
        "--requirement",
        str(lock_path),
        "--disable-pip",
    ]
    assert command.count("--ignore-vuln") == 2
    assert command[-2:] == ["--format", "cyclonedx-json"]
