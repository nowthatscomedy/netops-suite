from __future__ import annotations

from datetime import datetime

LOG_TIME_FORMAT = "%H:%M:%S"


def timestamp_lines(message: object, now: datetime | None = None) -> str:
    """Prefix every non-blank line with [HH:MM:SS] so on-screen and saved logs show when it happened."""
    stamp = (now or datetime.now()).strftime(LOG_TIME_FORMAT)
    return "\n".join(
        f"[{stamp}] {line}" if line.strip() else line
        for line in str(message).replace("\r\n", "\n").split("\n")
    )


def append_timestamped(widget, message: object) -> None:
    widget.appendPlainText(timestamp_lines(message))
