from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app.models.result_models import OperationResult, PingResult
from app.utils.parser import parse_target_entries
from app.utils.process_utils import no_window_creationflags, windows_console_encoding


class PingService:
    REPLY_PATTERN = re.compile(r"(?:time|시간)\s*[=<]?\s*(\d+)\s*ms", re.IGNORECASE)
    TIMEOUT_MARKERS = ("Request timed out", "요청 시간이 만료", "일반 오류", "General failure")
    UNREACHABLE_MARKERS = ("Destination host unreachable", "대상 호스트에 연결할 수 없습니다")

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def run_multi_ping(
        self,
        raw_targets: str,
        count: int,
        timeout_ms: int,
        max_workers: int,
        continuous: bool = False,
        progress_callback=None,
        cancel_event=None,
    ) -> list[PingResult]:
        targets = parse_target_entries(raw_targets)
        if not targets:
            raise ValueError("최소 1개 이상의 Ping 대상을 입력해 주세요.")

        results: list[PingResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._ping_target,
                    name,
                    target,
                    count,
                    timeout_ms,
                    continuous,
                    progress_callback,
                    cancel_event,
                ): (name, target)
                for name, target in targets
            }
            for future in as_completed(future_map):
                result = future.result()
                results.append(result)
        return sorted(results, key=lambda item: (item.name.lower(), item.target.lower()))

    def quick_ping(self, target: str, count: int = 2, timeout_ms: int = 4000) -> OperationResult:
        result = self._ping_target(target, target, count, timeout_ms, False, None, None)
        if result.success:
            summary = (
                f"{result.target}: {result.status}, 손실 {result.packet_loss:.0f}%, "
                f"RTT {result.min_rtt or 0:.1f}/{result.avg_rtt or 0:.1f}/{result.max_rtt or 0:.1f} ms"
            )
            return OperationResult(True, summary)
        return OperationResult(False, f"{result.target}: {result.status}", result.error)

    def _ping_target(
        self,
        name: str,
        target: str,
        count: int,
        timeout_ms: int,
        continuous: bool,
        progress_callback,
        cancel_event,
    ) -> PingResult:
        command = ["ping", target, "-w", str(timeout_ms)]
        command.extend(["-t"] if continuous else ["-n", str(count)])
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
        stats = {"sent": 0, "received": 0, "rtts": [], "last_seen": "", "last_status": "대기"}
        output_lines: list[str] = []

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

            output_lines.append(item)
            self._consume_ping_line(name, target, item, stats, progress_callback)

            if not continuous and stats["sent"] >= count and process.poll() is not None:
                break

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

        sent = int(stats["sent"])
        received = int(stats["received"])
        rtts = list(stats["rtts"])
        packet_loss = round(((sent - received) / sent) * 100, 2) if sent else 100.0
        status = "정상" if received == sent and sent > 0 else "일부 손실" if received > 0 else "실패"
        error = ""
        if sent == 0:
            error = "Ping 결과를 해석하지 못했습니다."
        elif cancel_event and cancel_event.is_set():
            status = "중지됨"

        return PingResult(
            name=name,
            target=target,
            success=received > 0,
            status=status,
            packet_loss=packet_loss,
            sent=sent,
            received=received,
            min_rtt=min(rtts) if rtts else None,
            avg_rtt=round(sum(rtts) / len(rtts), 2) if rtts else None,
            max_rtt=max(rtts) if rtts else None,
            last_seen=str(stats["last_seen"]),
            error=error,
        )

    def _consume_ping_line(self, name: str, target: str, line: str, stats: dict, progress_callback) -> None:
        stripped = line.strip()
        if not stripped:
            return

        reply_match = self.REPLY_PATTERN.search(stripped)
        timestamp = datetime.now().strftime("%H:%M:%S")

        if reply_match:
            rtt = float(reply_match.group(1))
            stats["sent"] += 1
            stats["received"] += 1
            stats["rtts"].append(rtt)
            stats["last_seen"] = timestamp
            stats["last_status"] = "정상"
        elif any(marker.lower() in stripped.lower() for marker in self.TIMEOUT_MARKERS):
            stats["sent"] += 1
            stats["last_seen"] = timestamp
            stats["last_status"] = "시간 초과"
        elif any(marker.lower() in stripped.lower() for marker in self.UNREACHABLE_MARKERS):
            stats["sent"] += 1
            stats["last_seen"] = timestamp
            stats["last_status"] = "도달 불가"
        else:
            return

        sent = int(stats["sent"])
        received = int(stats["received"])
        rtts = list(stats["rtts"])
        packet_loss = round(((sent - received) / sent) * 100, 2) if sent else 100.0
        result = PingResult(
            name=name,
            target=target,
            success=received > 0,
            status=str(stats["last_status"]),
            packet_loss=packet_loss,
            sent=sent,
            received=received,
            min_rtt=min(rtts) if rtts else None,
            avg_rtt=round(sum(rtts) / len(rtts), 2) if rtts else None,
            max_rtt=max(rtts) if rtts else None,
            last_seen=str(stats["last_seen"]),
        )
        if progress_callback is not None:
            progress_callback.emit({"type": "ping", "result": result, "line": f"[{timestamp}] {stripped}"})
