from __future__ import annotations

import logging
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.models.result_models import TcpCheckResult
from app.utils.parser import parse_port_list, parse_target_entries
from app.utils.process_utils import no_window_creationflags, windows_console_encoding


class TcpCheckService:
    TCPING_PATTERN = re.compile(
        r"(?P<timestamp>\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}).*?(?P<status>Port is open|No response).*?time=(?P<time>\d+\.?\d*)ms",
        re.IGNORECASE,
    )
    SUMMARY_SENT_PATTERN = re.compile(r"(?P<sent>\d+)\s+probes sent", re.IGNORECASE)
    SUMMARY_SUCCESS_PATTERN = re.compile(r"(?P<ok>\d+)\s+successful,\s+(?P<fail>\d+)\s+failed", re.IGNORECASE)

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def _find_tcping(self) -> str | None:
        return shutil.which("tcping.exe") or shutil.which("tcping")

    def run_multi_check(
        self,
        raw_targets: str,
        raw_ports: str,
        count: int,
        timeout_ms: int,
        max_workers: int,
        continuous: bool = False,
        progress_callback=None,
        cancel_event=None,
    ) -> list[TcpCheckResult]:
        targets = parse_target_entries(raw_targets)
        ports = parse_port_list(raw_ports)
        if not targets:
            raise ValueError("최소 1개 이상의 TCP 대상을 입력해 주세요.")

        worker_count = max(1, min(max_workers, len(targets) * len(ports)))
        results: list[TcpCheckResult] = []
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(
                    self._run_single_check,
                    name,
                    target,
                    port,
                    count,
                    timeout_ms,
                    continuous,
                    progress_callback,
                    cancel_event,
                ): (name, target, port)
                for name, target in targets
                for port in ports
            }
            for future in as_completed(future_map):
                results.append(future.result())
        return sorted(results, key=lambda item: (item.name.lower(), item.target.lower(), item.port))

    def _run_single_check(
        self,
        name: str,
        target: str,
        port: int,
        count: int,
        timeout_ms: int,
        continuous: bool,
        progress_callback,
        cancel_event,
    ) -> TcpCheckResult:
        tcping_path = self._find_tcping()
        if tcping_path:
            return self._run_tcping(
                tcping_path,
                name,
                target,
                port,
                count,
                timeout_ms,
                continuous,
                progress_callback,
                cancel_event,
            )
        return self._run_socket_check(name, target, port, count, timeout_ms, continuous, progress_callback, cancel_event)

    def _run_tcping(
        self,
        tcping_path: str,
        name: str,
        target: str,
        port: int,
        count: int,
        timeout_ms: int,
        continuous: bool,
        progress_callback,
        cancel_event,
    ) -> TcpCheckResult:
        command = [tcping_path, "-d", "-w", f"{max(timeout_ms / 1000, 0.1):.3f}"]
        command.extend(["-t"] if continuous else ["-n", str(count)])
        command.extend([target, str(port)])

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            creationflags=no_window_creationflags(),
            bufsize=1,
        )

        output_queue: queue.Queue[str | None] = queue.Queue()
        sent = 0
        successful = 0
        measurements: list[float] = []
        last_seen = ""
        last_status = "대기"
        summary_sent = 0
        summary_successful = 0
        summary_failed = 0

        def reader() -> None:
            try:
                if process.stdout is None:
                    output_queue.put(None)
                    return
                for line in iter(process.stdout.readline, ""):
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        threading.Thread(target=reader, daemon=True).start()
        reader_finished = False

        while True:
            if cancel_event and cancel_event.is_set():
                process.kill()
                break

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

            stripped = item.strip()
            if not stripped:
                continue

            sent_match = self.SUMMARY_SENT_PATTERN.search(stripped)
            if sent_match:
                summary_sent = int(sent_match.group("sent"))
                continue

            success_match = self.SUMMARY_SUCCESS_PATTERN.search(stripped)
            if success_match:
                summary_successful = int(success_match.group("ok"))
                summary_failed = int(success_match.group("fail"))
                continue

            match = self.TCPING_PATTERN.search(stripped)
            if not match:
                continue

            sent += 1
            elapsed_ms = float(match.group("time"))
            measurements.append(elapsed_ms)
            last_seen = match.group("timestamp").split()[-1]
            if "open" in match.group("status").lower():
                successful += 1
                last_status = "열림"
            else:
                last_status = "응답 없음"

            result = TcpCheckResult(
                name=name,
                target=target,
                port=port,
                status=last_status,
                sent=sent,
                successful=successful,
                failed=max(sent - successful, 0),
                packet_loss=round(((sent - successful) / sent) * 100, 2) if sent else 0.0,
                min_response_ms=min(measurements) if measurements else None,
                response_ms=round(sum(measurements) / len(measurements), 2) if measurements else None,
                max_response_ms=max(measurements) if measurements else None,
                last_seen=last_seen,
            )
            if progress_callback is not None:
                progress_callback.emit({"type": "tcp", "result": result, "line": f"[{last_seen}] {stripped}"})

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

        if summary_sent:
            sent = summary_sent
        if summary_successful or (summary_sent and summary_successful == 0):
            successful = summary_successful
        if summary_failed or (summary_sent and summary_failed == 0):
            failed = summary_failed
        else:
            failed = max(sent - successful, 0)

        status = "열림" if successful > 0 and successful == sent else "부분 응답" if successful > 0 else "응답 없음"
        if cancel_event and cancel_event.is_set():
            status = "중지됨"

        return TcpCheckResult(
            name=name,
            target=target,
            port=port,
            status=status,
            sent=sent,
            successful=successful,
            failed=failed,
            packet_loss=round((failed / sent) * 100, 2) if sent else 0.0,
            min_response_ms=min(measurements) if measurements else None,
            response_ms=round(sum(measurements) / len(measurements), 2) if measurements else None,
            max_response_ms=max(measurements) if measurements else None,
            last_seen=last_seen,
            error="" if sent > 0 else "tcping 출력 결과를 해석하지 못했습니다.",
        )

    def _run_socket_check(
        self,
        name: str,
        target: str,
        port: int,
        count: int,
        timeout_ms: int,
        continuous: bool,
        progress_callback,
        cancel_event,
    ) -> TcpCheckResult:
        timeout_seconds = max(timeout_ms / 1000.0, 0.1)
        sent = 0
        successful = 0
        measurements: list[float] = []
        last_seen = ""
        last_error = ""

        while True:
            if cancel_event and cancel_event.is_set():
                break
            if not continuous and sent >= count:
                break

            started = time.perf_counter()
            success = False
            try:
                with socket.create_connection((target, port), timeout=timeout_seconds):
                    success = True
            except OSError as exc:
                last_error = str(exc)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

            sent += 1
            measurements.append(elapsed_ms)
            if success:
                successful += 1
            last_seen = datetime.now().strftime("%H:%M:%S")
            status = "열림" if success else "응답 없음"
            result = TcpCheckResult(
                name=name,
                target=target,
                port=port,
                status=status,
                sent=sent,
                successful=successful,
                failed=max(sent - successful, 0),
                packet_loss=round(((sent - successful) / sent) * 100, 2) if sent else 0.0,
                min_response_ms=min(measurements) if measurements else None,
                response_ms=round(sum(measurements) / len(measurements), 2),
                max_response_ms=max(measurements) if measurements else None,
                last_seen=last_seen,
                error="" if success else last_error,
            )
            if progress_callback is not None:
                line = f"[{last_seen}] {target}:{port} {'연결 성공' if success else '응답 없음'} ({elapsed_ms:.2f} ms)"
                progress_callback.emit({"type": "tcp", "result": result, "line": line})

            if cancel_event and cancel_event.is_set():
                break
            if continuous or sent < count:
                time.sleep(1)

        status = "열림" if successful > 0 and successful == sent else "부분 응답" if successful > 0 else "응답 없음"
        if cancel_event and cancel_event.is_set():
            status = "중지됨"
        return TcpCheckResult(
            name=name,
            target=target,
            port=port,
            status=status,
            sent=sent,
            successful=successful,
            failed=max(sent - successful, 0),
            packet_loss=round(((sent - successful) / sent) * 100, 2) if sent else 0.0,
            min_response_ms=min(measurements) if measurements else None,
            response_ms=round(sum(measurements) / len(measurements), 2) if measurements else None,
            max_response_ms=max(measurements) if measurements else None,
            last_seen=last_seen,
            error="" if successful > 0 else last_error,
        )
