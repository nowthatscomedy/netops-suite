from __future__ import annotations

from pathlib import Path

from scripts import audit_dependencies


def test_dependency_audit_has_no_vulnerability_exceptions():
    command = audit_dependencies.build_pip_audit_command()

    assert "--ignore-vuln" not in command
    assert not hasattr(audit_dependencies, "PARAMIKO_EXCEPTION_IDS")


def test_runtime_requirements_use_fixed_paramiko_with_compatible_netmiko():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    runtime_lock = Path("requirements-lock.txt").read_text(encoding="utf-8")

    assert "paramiko>=5,<6" in requirements
    assert "netmiko>=4.8,<5" in requirements
    assert "paramiko==5." in runtime_lock
    assert "netmiko==4.8." in runtime_lock


def test_dependency_audit_command_uses_current_environment():
    command = audit_dependencies.build_pip_audit_command(["--format", "json"])

    assert command[:3] == [
        audit_dependencies.sys.executable,
        "-m",
        "pip_audit",
    ]
    assert "--local" in command
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
    assert command[-2:] == ["--format", "cyclonedx-json"]
