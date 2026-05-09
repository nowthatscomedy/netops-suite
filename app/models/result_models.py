from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return not self.timed_out and self.returncode == 0


@dataclass(slots=True)
class OperationResult:
    success: bool
    message: str
    details: str = ""
    payload: Any = None


@dataclass(slots=True)
class PingResult:
    name: str
    target: str
    success: bool
    status: str
    packet_loss: float
    sent: int = 0
    received: int = 0
    min_rtt: float | None = None
    avg_rtt: float | None = None
    max_rtt: float | None = None
    last_seen: str = ""
    error: str = ""


@dataclass(slots=True)
class TcpCheckResult:
    name: str
    target: str
    port: int
    status: str
    sent: int = 0
    successful: int = 0
    failed: int = 0
    packet_loss: float = 0.0
    min_response_ms: float | None = None
    response_ms: float | None = None
    max_response_ms: float | None = None
    last_seen: str = ""
    error: str = ""
