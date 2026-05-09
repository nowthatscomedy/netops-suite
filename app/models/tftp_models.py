from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class TftpTransferResult:
    action: str
    source_path: str
    target_path: str
    size_bytes: int = 0
    transferred_bytes: int = 0
    duration_seconds: float = 0.0
    status: str = "pending"
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

    @property
    def progress_text(self) -> str:
        if self.size_bytes > 0:
            return f"{self.transferred_bytes:,}/{self.size_bytes:,}"
        return f"{self.transferred_bytes:,}" if self.transferred_bytes else "-"

    @property
    def size_text(self) -> str:
        return f"{self.size_bytes:,}" if self.size_bytes else "-"

    @property
    def duration_text(self) -> str:
        return f"{self.duration_seconds:.2f}s" if self.duration_seconds else "-"


@dataclass(slots=True)
class TftpServerRuntime:
    bind_host: str
    port: int
    root_folder: str
    read_only: bool
    session_count: int = 0

    def to_dict(self) -> dict:
        return {
            "bind_host": self.bind_host,
            "port": self.port,
            "root_folder": self.root_folder,
            "read_only": self.read_only,
            "session_count": self.session_count,
        }
