from __future__ import annotations

import ctypes
import locale
import os
import queue
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

from app.models.result_models import OperationResult


def windows_console_encoding() -> str:
    if os.name == "nt":
        return "oem"
    encoding = locale.getpreferredencoding(False)
    if not encoding:
        return "utf-8"
    return encoding


def decode_windows_command_output(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if not output:
        return ""

    preferred = locale.getpreferredencoding(False) or "utf-8"
    candidates: list[str] = []
    seen: set[str] = set()
    for encoding in ("utf-8", preferred, "mbcs", "oem", "cp949", "euc-kr", "cp1252"):
        normalized = encoding.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(encoding)

    for encoding in candidates:
        try:
            return output.decode(encoding)
        except UnicodeDecodeError:
            continue
    return output.decode("utf-8", errors="replace")


def no_window_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _windows_system_directory() -> Path | None:
    """Return the OS-reported Windows system directory, or fail closed."""

    if os.name != "nt":
        return None
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return None
    try:
        kernel32 = loader("kernel32", use_last_error=True)
        get_system_directory = kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = (wintypes.LPWSTR, wintypes.UINT)
        get_system_directory.restype = wintypes.UINT
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        length = int(get_system_directory(buffer, capacity))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if length <= 0 or length >= capacity or not buffer.value:
        return None
    directory = Path(buffer.value)
    if not directory.is_absolute():
        return None
    return directory


def terminate_process_tree(pid: int) -> bool:
    """Terminate one Windows process tree without invoking a command shell.

    CLI launchers may create child processes. Killing only their wrapper can
    leave model discovery or chat work running, so cancellation uses the
    system ``taskkill.exe`` with a numeric PID and fixed arguments.
    """

    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return False
    system_directory = _windows_system_directory()
    if system_directory is None:
        return False
    taskkill = system_directory / "taskkill.exe"
    if not taskkill.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            shell=False,
            creationflags=no_window_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return completed.returncode == 0


def command_exists(command_name: str) -> bool:
    return shutil.which(command_name) is not None


def run_streaming_command(
    command: list[str],
    timeout: int,
    progress_callback=None,
    cancel_event=None,
    encoding: str | None = None,
) -> OperationResult:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=encoding or windows_console_encoding(),
        errors="replace",
        creationflags=no_window_creationflags(),
        bufsize=1,
    )

    output_queue: queue.Queue[str | None] = queue.Queue()
    output_lines: list[str] = []
    started = time.time()

    def reader() -> None:
        try:
            if process.stdout is None:
                output_queue.put(None)
                return
            for line in iter(process.stdout.readline, ""):
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    reader_finished = False

    while True:
        if cancel_event and cancel_event.is_set():
            process.kill()
            process.wait(timeout=5)
            return OperationResult(False, "작업이 취소되었습니다.", "".join(output_lines))

        if time.time() - started > timeout:
            process.kill()
            process.wait(timeout=5)
            return OperationResult(False, "명령 실행 시간이 초과되었습니다.", "".join(output_lines))

        try:
            item = output_queue.get(timeout=0.2)
        except queue.Empty:
            if reader_finished and process.poll() is not None:
                break
            continue

        if item is None:
            reader_finished = True
            if process.poll() is not None:
                break
            continue

        output_lines.append(item)
        if progress_callback is not None:
            progress_callback.emit(item.rstrip("\r\n"))

    process.wait(timeout=5)
    output_text = "".join(output_lines)
    success = process.returncode == 0
    return OperationResult(success, "명령이 완료되었습니다." if success else "명령 실행에 실패했습니다.", output_text)
