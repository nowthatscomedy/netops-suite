from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.i18n import t


class TuiDashboard:
    """Render a live dashboard for inspection/backup tasks."""

    def __init__(self, mode: str, total_devices: int) -> None:
        self.mode = mode
        self.total_devices = total_devices
        self.completed = 0
        self.success = 0
        self.fail = 0
        self.started_at = datetime.now()
        self.recent_logs: deque[str] = deque(maxlen=12)
        self._live: Live | None = None
        self._lock = threading.Lock()
        self._completed = False
        self._last_updated_at = self.started_at
        self._log_handler: _DashboardLogHandler | None = None
        self._saved_console_handlers: list[logging.Handler] = []

    def start(self) -> None:
        if self._live is not None:
            return
        self._live = Live(self._render(), refresh_per_second=5, transient=False)
        self._live.start()
        self._attach_log_handler()

    def mark_completed(self, note: str | None = None) -> None:
        with self._lock:
            self._completed = True
            self.recent_logs.append(note or t("dashboard.default_completed_note"))
            self._refresh()

    def stop(self) -> None:
        self._detach_log_handler()
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    def _attach_log_handler(self) -> None:
        root = logging.getLogger()
        self._log_handler = _DashboardLogHandler(self)
        self._log_handler.setLevel(logging.INFO)
        self._log_handler.setFormatter(
            logging.Formatter("[%(threadName)s] %(levelname)s | %(message)s"),
        )
        self._saved_console_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
        ]
        for handler in self._saved_console_handlers:
            root.removeHandler(handler)
        root.addHandler(self._log_handler)

    def _detach_log_handler(self) -> None:
        root = logging.getLogger()
        if self._log_handler is not None:
            root.removeHandler(self._log_handler)
            self._log_handler = None
        for handler in self._saved_console_handlers:
            root.addHandler(handler)
        self._saved_console_handlers = []

    def handle_event(self, event: dict[str, object]) -> None:
        with self._lock:
            event_type = str(event.get("type", ""))
            if event_type == "device_complete":
                if event.get("success"):
                    self.success += 1
                else:
                    self.fail += 1
                self.completed = self.success + self.fail
                self._last_updated_at = datetime.now()
                self._refresh()
                return

            message = str(event.get("message", ""))
            if not message:
                return
            self._last_updated_at = datetime.now()
            self.recent_logs.append(message)
            self._refresh()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    @staticmethod
    def _bar(done: int, total: int, width: int = 28) -> Text:
        if total <= 0:
            return Text("0/0 (0%)")
        ratio = max(0.0, min(1.0, done / total))
        percent = int(ratio * 100)
        safe_done = max(0, min(done, total))
        filled = int(width * ratio)
        empty = width - filled
        bar = Text()
        bar.append("[", style="dim")
        if filled > 0:
            bar.append("#" * filled, style="bold green")
        if empty > 0:
            bar.append("-" * empty, style="grey50")
        bar.append("]", style="dim")
        bar.append(f" {safe_done}/{total} ({percent}%)", style="bold")
        return bar

    @staticmethod
    def _format_success_fail(success: int, fail: int, completed: int) -> Text:
        text = Text()
        text.append(str(success), style="bold green")
        text.append(f" {t('dashboard.labels.success')}", style="green")
        text.append(" / ", style="dim")
        text.append(str(fail), style="bold red")
        text.append(f" {t('dashboard.labels.failed')}", style="red")
        if completed > 0:
            rate = success / completed * 100.0
            text.append("  (", style="dim")
            if rate >= 80:
                text.append(f"{rate:.1f}%", style="bold green")
            elif rate >= 50:
                text.append(f"{rate:.1f}%", style="bold yellow")
            else:
                text.append(f"{rate:.1f}%", style="bold red")
            text.append(")", style="dim")
        return text

    def _render(self) -> Group:
        elapsed = datetime.now() - self.started_at
        elapsed_seconds = max(1.0, elapsed.total_seconds())
        completed = max(0, self.completed)
        remaining = max(0, self.total_devices - completed)
        throughput_per_min = (completed / elapsed_seconds) * 60.0

        if self._completed:
            status_text = Text(t("dashboard.status.completed"), style="bold green")
        elif completed > 0:
            status_text = Text(t("dashboard.status.in_progress"), style="bold yellow")
        else:
            status_text = Text(t("dashboard.status.preparing"), style="bold cyan")

        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="cyan")
        summary.add_column(style="bold")
        summary.add_row(t("dashboard.labels.mode"), self.mode)
        summary.add_row(t("dashboard.labels.status"), status_text)
        summary.add_row(t("dashboard.labels.elapsed"), str(elapsed).split(".")[0])
        summary.add_row(
            t("dashboard.labels.progress"),
            self._bar(self.completed, self.total_devices),
        )
        summary.add_row(
            t("dashboard.labels.success_fail"),
            self._format_success_fail(self.success, self.fail, completed),
        )
        summary.add_row(t("dashboard.labels.remaining"), Text(str(remaining), style="bold cyan"))
        summary.add_row(
            t("dashboard.labels.throughput"),
            Text(
                f"{throughput_per_min:.2f} {t('dashboard.units.devices_per_min')}",
                style="bold magenta",
            ),
        )
        summary.add_row(
            t("dashboard.labels.last_updated"),
            self._last_updated_at.strftime("%H:%M:%S"),
        )

        event_lines = "\n".join(self.recent_logs) if self.recent_logs else t("dashboard.no_events")
        event_text = Text(event_lines, overflow="ellipsis")
        return Group(
            Panel(
                summary,
                title=f"[bold green]{t('dashboard.titles.dashboard')}[/bold green]",
                border_style="green",
            ),
            Panel(
                event_text,
                title=f"[bold cyan]{t('dashboard.titles.recent_events')}[/bold cyan]",
                border_style="cyan",
            ),
        )


class _DashboardLogHandler(logging.Handler):
    def __init__(self, dashboard: TuiDashboard) -> None:
        super().__init__()
        self._dashboard = dashboard

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self._dashboard.handle_event({"type": "log", "message": message})
        except Exception:
            self.handleError(record)
