from __future__ import annotations

import subprocess
from pathlib import Path

from app.utils import process_utils


class _FakeGetSystemDirectory:
    def __init__(self, value: str, result: int | None = None) -> None:
        self.value = value
        self.result = len(value) if result is None else result
        self.argtypes = None
        self.restype = None

    def __call__(self, buffer, _size: int) -> int:
        buffer.value = self.value
        return self.result


class _FakeKernel32:
    def __init__(self, get_system_directory: _FakeGetSystemDirectory) -> None:
        self.GetSystemDirectoryW = get_system_directory


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _install_fake_system_directory(monkeypatch, value: str, result: int | None = None) -> None:
    function = _FakeGetSystemDirectory(value, result)
    monkeypatch.setattr(
        process_utils.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: _FakeKernel32(function),
        raising=False,
    )


def test_terminate_process_tree_uses_system_directory_api_and_fixed_argv(
    monkeypatch, tmp_path: Path
) -> None:
    trusted_directory = tmp_path / "trusted-system32"
    trusted_directory.mkdir()
    trusted_taskkill = trusted_directory / "taskkill.exe"
    trusted_taskkill.write_bytes(b"placeholder")
    evil_root = tmp_path / "attacker-controlled"
    monkeypatch.setenv("SystemRoot", str(evil_root))
    monkeypatch.setenv("PATH", str(evil_root))
    monkeypatch.setattr(process_utils.os, "name", "nt")
    _install_fake_system_directory(monkeypatch, str(trusted_directory))
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Completed(0)

    monkeypatch.setattr(process_utils.subprocess, "run", fake_run)

    assert process_utils.terminate_process_tree(4321) is True
    assert captured["argv"] == [
        str(trusted_taskkill),
        "/PID",
        "4321",
        "/T",
        "/F",
    ]
    assert captured["kwargs"] == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 10,
        "check": False,
        "shell": False,
        "creationflags": process_utils.no_window_creationflags(),
    }
    assert str(evil_root) not in str(captured["argv"])


def test_terminate_process_tree_rejects_invalid_pid_and_non_windows(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(process_utils.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    monkeypatch.setattr(process_utils.os, "name", "posix")
    assert process_utils.terminate_process_tree(123) is False

    monkeypatch.setattr(process_utils.os, "name", "nt")
    for invalid in (0, -1, True, 1.5, "123", None):
        assert process_utils.terminate_process_tree(invalid) is False

    assert calls == []


def test_terminate_process_tree_fails_closed_when_system_directory_api_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(process_utils.os, "name", "nt")

    def fail_loader(*_args, **_kwargs):
        raise OSError("kernel32 unavailable")

    monkeypatch.setattr(process_utils.ctypes, "WinDLL", fail_loader, raising=False)
    monkeypatch.setattr(
        process_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert process_utils.terminate_process_tree(123) is False


def test_terminate_process_tree_fails_closed_for_invalid_api_result(monkeypatch) -> None:
    monkeypatch.setattr(process_utils.os, "name", "nt")
    _install_fake_system_directory(monkeypatch, "", result=0)

    assert process_utils.terminate_process_tree(123) is False


def test_terminate_process_tree_fails_closed_when_taskkill_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(process_utils.os, "name", "nt")
    _install_fake_system_directory(monkeypatch, str(tmp_path / "missing"))
    monkeypatch.setattr(
        process_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert process_utils.terminate_process_tree(123) is False


def test_terminate_process_tree_handles_subprocess_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(process_utils.os, "name", "nt")
    taskkill = tmp_path / "taskkill.exe"
    taskkill.write_bytes(b"placeholder")
    _install_fake_system_directory(monkeypatch, str(tmp_path))

    for error in (
        OSError("launch failed"),
        subprocess.TimeoutExpired("taskkill", 10),
        ValueError("invalid launch"),
    ):
        monkeypatch.setattr(
            process_utils.subprocess,
            "run",
            lambda *_args, _error=error, **_kwargs: (_ for _ in ()).throw(_error),
        )
        assert process_utils.terminate_process_tree(123) is False


def test_terminate_process_tree_returns_false_for_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(process_utils.os, "name", "nt")
    taskkill = tmp_path / "taskkill.exe"
    taskkill.write_bytes(b"placeholder")
    _install_fake_system_directory(monkeypatch, str(tmp_path))
    monkeypatch.setattr(
        process_utils.subprocess,
        "run",
        lambda *_args, **_kwargs: _Completed(1),
    )

    assert process_utils.terminate_process_tree(123) is False
