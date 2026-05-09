from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.models.result_models import OperationResult
from app.utils.file_utils import AppPaths
from app.utils.process_utils import (
    command_exists,
    no_window_creationflags,
    run_streaming_command,
    windows_console_encoding,
)


class IperfService:
    DOWNLOAD_URL = "https://github.com/esnet/iperf/releases"
    WINGET_PACKAGE_ID = "ar51an.iPerf3"
    WINGET_PACKAGE_URL = "https://github.com/ar51an/iperf3-win-builds"
    WINGET_TIMEOUT_SEC = 1800
    WINGET_ENCODING = "utf-8"
    NO_UPGRADE_MARKERS = (
        "No applicable upgrade found",
        "No available upgrade found",
        "No newer package versions are available from the configured sources",
        "설치된 패키지를 찾지 못했습니다",
        "업그레이드 가능한 패키지를 찾지 못했습니다",
        "이미 최신 버전입니다",
        "최신 버전",
    )

    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger

    def _winget_link_candidates(self) -> list[Path]:
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_appdata:
            return []
        base = Path(local_appdata) / "Microsoft" / "WinGet" / "Links"
        return [base / "iperf3.exe", base / "iperf3"]

    def _winget_package_candidates(self) -> list[Path]:
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_appdata:
            return []

        packages_root = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        if not packages_root.exists():
            return []

        candidates: list[Path] = []
        for package_dir in packages_root.glob(f"{self.WINGET_PACKAGE_ID}_*"):
            candidate = package_dir / "iperf3.exe"
            if candidate.exists():
                candidates.append(candidate)
        return candidates

    def managed_executable_path(self) -> str | None:
        for candidate in self._winget_link_candidates():
            if candidate.exists():
                return str(candidate)
        for candidate in self._winget_package_candidates():
            if candidate.exists():
                return str(candidate)
        return None

    def executable_details(self) -> tuple[str | None, str]:
        bundled = self.paths.root / "iperf3.exe"
        if bundled.exists():
            return str(bundled), "program folder"

        for candidate in self._winget_link_candidates():
            if candidate.exists():
                return str(candidate), "winget"

        for candidate in self._winget_package_candidates():
            if candidate.exists():
                return str(candidate), "winget"

        system_path = shutil.which("iperf3.exe") or shutil.which("iperf3")
        if system_path:
            normalized = str(Path(system_path))
            winget_targets = {
                str(candidate) for candidate in [*self._winget_link_candidates(), *self._winget_package_candidates()]
            }
            if normalized in winget_targets:
                return normalized, "winget"
            return normalized, "system PATH"

        return None, ""

    def executable_path(self) -> str | None:
        path, _source = self.executable_details()
        return path

    def executable_version(self, executable_path: str | None = None) -> str | None:
        path = executable_path or self.executable_path()
        if not path:
            return None

        try:
            completed = self._run_capture([path, "--version"])
        except OSError as exc:
            self.logger.warning("Failed to run iperf3 --version: %s", exc)
            return None

        output = self._combined_output(completed)
        if not output:
            return None

        first_line = output.splitlines()[0].strip()
        match = re.search(r"\biperf(?:3)?\s+([0-9A-Za-z.\-_+]+)", first_line, re.IGNORECASE)
        if match:
            return match.group(1)
        return first_line or None

    def is_available(self) -> bool:
        return self.executable_path() is not None

    def managed_install_supported(self) -> bool:
        return command_exists("winget")

    def managed_package_page(self) -> str:
        return self.WINGET_PACKAGE_URL

    def _emit_progress(self, progress_callback: Any, message: str) -> None:
        if not message or progress_callback is None:
            return
        emitter = getattr(progress_callback, "emit", None)
        if callable(emitter):
            emitter(message)
            return
        if callable(progress_callback):
            progress_callback(message)

    def _run_capture(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        encoding = self.WINGET_ENCODING if command and command[0].lower() == "winget" else windows_console_encoding()
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            creationflags=no_window_creationflags(),
            check=False,
        )

    def _combined_output(self, completed: subprocess.CompletedProcess[str]) -> str:
        return "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()

    def is_managed_installed(self) -> bool:
        if not self.managed_install_supported():
            return False

        command = [
            "winget",
            "list",
            "--id",
            self.WINGET_PACKAGE_ID,
            "--exact",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        completed = self._run_capture(command)
        output = self._combined_output(completed)

        if completed.returncode == 0 and self.WINGET_PACKAGE_ID.lower() in output.lower():
            return True

        executable_path, source = self.executable_details()
        return bool(executable_path and source == "winget")

    def is_managed_update_available(self) -> bool:
        if not self.managed_install_supported():
            return False

        command = [
            "winget",
            "upgrade",
            "--id",
            self.WINGET_PACKAGE_ID,
            "--exact",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        completed = self._run_capture(command)
        output = self._combined_output(completed)

        if completed.returncode == 0 and self.WINGET_PACKAGE_ID.lower() in output.lower():
            return True

        if self._is_no_upgrade_result(output):
            return False

        self.logger.info(
            "Could not confirm managed iperf3 upgrade state from winget output. rc=%s output=%s",
            completed.returncode,
            output,
        )
        return False

    def managed_install_state(self) -> dict[str, object]:
        available = self.managed_install_supported()
        installed = self.is_managed_installed() if available else False
        update_available = self.is_managed_update_available() if available and installed else False

        if not available:
            action_label = "winget 없음"
            button_enabled = False
        elif not installed:
            action_label = "winget 설치"
            button_enabled = True
        elif update_available:
            action_label = "winget 업데이트"
            button_enabled = True
        else:
            action_label = "최신 버전 사용 중"
            button_enabled = False

        return {
            "available": available,
            "installed": installed,
            "update_available": update_available,
            "button_enabled": button_enabled,
            "action_label": action_label,
            "package_id": self.WINGET_PACKAGE_ID,
            "package_url": self.managed_package_page(),
        }

    def _is_no_upgrade_result(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker.lower() in lowered for marker in self.NO_UPGRADE_MARKERS)

    def install_or_update_managed(
        self,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        if not self.managed_install_supported():
            return OperationResult(
                False,
                "winget을 찾지 못했습니다.",
                "Microsoft App Installer(winget)가 없는 환경이어서 프로그램 내부 설치를 진행할 수 없습니다.",
            )

        installed = self.is_managed_installed()
        action = "업데이트" if installed else "설치"
        command = [
            "winget",
            "upgrade" if installed else "install",
            "--id",
            self.WINGET_PACKAGE_ID,
            "--exact",
            "--scope",
            "user",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ]

        self.logger.info("Starting managed iperf3 %s via winget package %s", action, self.WINGET_PACKAGE_ID)
        self._emit_progress(progress_callback, f"winget {action} 시작: {self.WINGET_PACKAGE_ID}")
        self._emit_progress(progress_callback, f"패키지 페이지: {self.managed_package_page()}")

        result = run_streaming_command(
            command,
            timeout=self.WINGET_TIMEOUT_SEC,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            encoding=self.WINGET_ENCODING,
        )
        output = result.details or ""

        if result.success:
            executable_path, source = self.executable_details()
            managed_path = self.managed_executable_path()
            source_text = {
                "program folder": "프로그램 폴더",
                "winget": "winget 관리형 설치",
                "system PATH": "시스템 PATH",
            }.get(source, source or "알 수 없음")
            if executable_path:
                message = f"iperf3 {action}을 완료했습니다."
                details_lines = [
                    f"현재 앱 사용 경로: {executable_path}",
                    f"현재 앱 사용 원본: {source_text}",
                ]
                if managed_path:
                    details_lines.insert(0, f"관리형 설치 경로: {managed_path}")
                if managed_path and executable_path != managed_path:
                    details_lines.append("참고: 프로그램 폴더 실행 파일이 우선순위가 더 높아서 현재 앱은 그 파일을 사용합니다.")
                details = "\n".join(details_lines)
                self.logger.info("Managed iperf3 %s completed: %s (%s)", action, executable_path, source)
                return OperationResult(
                    True,
                    message,
                    details,
                    payload={
                        "path": executable_path,
                        "source": source,
                        "managed_path": managed_path,
                    },
                )

            self.logger.warning("Managed iperf3 %s succeeded but executable path was not detected yet.", action)
            return OperationResult(
                True,
                f"iperf3 {action}은 완료됐지만 실행 파일 경로를 바로 확인하지 못했습니다.",
                (
                    (output + "\n\n") if output else ""
                )
                + "상태 새로고침을 다시 누르거나 프로그램을 재실행한 뒤 확인해 주세요.",
            )

        if installed and self._is_no_upgrade_result(output):
            self.logger.info("Managed iperf3 upgrade skipped because the package is already current.")
            return OperationResult(True, "이미 최신 iperf3가 설치되어 있습니다.", output)

        self.logger.error("Managed iperf3 %s failed: %s", action, output)
        return OperationResult(False, f"winget으로 iperf3 {action}에 실패했습니다.", output)

    def run_test(
        self,
        mode: str,
        server: str,
        port: int,
        streams: int,
        duration: int,
        reverse: bool = False,
        udp: bool = False,
        ipv6: bool = False,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        executable = self.executable_path()
        if not executable:
            return OperationResult(
                False,
                "iperf3 실행 파일을 찾지 못했습니다.",
                (
                    "다음 방법 중 하나로 iperf3를 준비해 주세요.\n"
                    f"1) 앱에서 {self.WINGET_PACKAGE_ID} 패키지로 설치/업데이트\n"
                    f"2) {self.paths.root} 폴더에 iperf3.exe 배치\n"
                    "3) 시스템 PATH에서 iperf3 / iperf3.exe를 찾을 수 있게 설치\n\n"
                    f"패키지 페이지: {self.managed_package_page()}\n"
                    f"공식 릴리스: {self.DOWNLOAD_URL}"
                ),
            )

        mode = mode.strip().lower()
        if mode == "server":
            command = [executable, "-s", "-p", str(port), "--forceflush"]
            if ipv6:
                command.append("-6")
            timeout = 86400
        else:
            if not server.strip():
                return OperationResult(False, "클라이언트 모드에서는 서버 주소가 필요합니다.")
            command = [
                executable,
                "-c",
                server.strip(),
                "-p",
                str(port),
                "-P",
                str(streams),
                "-t",
                str(duration),
                "--forceflush",
            ]
            if reverse:
                command.append("-R")
            if udp:
                command.append("-u")
            if ipv6:
                command.append("-6")
            timeout = max(duration + 30, 60)

        self.logger.info("Starting iperf3 %s mode using %s", mode, executable)
        return run_streaming_command(
            command,
            timeout=timeout,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
