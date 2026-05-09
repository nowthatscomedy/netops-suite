from __future__ import annotations

import logging
import subprocess

from app.models.result_models import OperationResult
from app.utils.process_utils import no_window_creationflags, run_streaming_command, windows_console_encoding


class TraceService:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def run_tracert(
        self,
        target: str,
        resolve_names: bool = True,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        command = ["tracert"]
        if not resolve_names:
            command.append("-d")
        command.append(target)
        return run_streaming_command(command, timeout=180, progress_callback=progress_callback, cancel_event=cancel_event)

    def run_pathping(
        self,
        target: str,
        resolve_names: bool = True,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        command = ["pathping"]
        if not resolve_names:
            command.append("-n")
        command.append(target)
        return run_streaming_command(command, timeout=900, progress_callback=progress_callback, cancel_event=cancel_event)

    def run_ipconfig_all(self) -> OperationResult:
        return self._run_once(["ipconfig", "/all"], timeout=60)

    def run_route_print(self) -> OperationResult:
        return self._run_once(["route", "print"], timeout=30)

    def run_arp_table(self) -> OperationResult:
        return self._run_once(["arp", "-a"], timeout=30)

    def _run_once(self, command: list[str], timeout: int) -> OperationResult:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            timeout=timeout,
            creationflags=no_window_creationflags(),
        )
        success = completed.returncode == 0
        return OperationResult(
            success,
            "명령이 완료되었습니다." if success else "명령 실행에 실패했습니다.",
            completed.stdout or completed.stderr,
        )
