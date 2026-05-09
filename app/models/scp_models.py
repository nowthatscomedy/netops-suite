from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ScpProfile:
    name: str
    host: str = ""
    port: int = 22
    username: str = ""
    remote_path: str = "."
    timeout_seconds: int = 15

    @classmethod
    def from_dict(cls, data: dict) -> "ScpProfile":
        return cls(
            name=str(data.get("name", "") or ""),
            host=str(data.get("host", "") or ""),
            port=int(data.get("port", 22) or 22),
            username=str(data.get("username", "") or ""),
            remote_path=str(data.get("remote_path", ".") or "."),
            timeout_seconds=int(data.get("timeout_seconds", 15) or 15),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "remote_path": self.remote_path,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(slots=True)
class ScpTransferResult:
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
        if self.size_bytes <= 0:
            return "-"
        return f"{self.transferred_bytes:,}/{self.size_bytes:,}"

    @property
    def size_text(self) -> str:
        return f"{self.size_bytes:,}" if self.size_bytes else "-"

    @property
    def duration_text(self) -> str:
        return f"{self.duration_seconds:.2f}s" if self.duration_seconds else "-"


@dataclass(slots=True)
class ScpServerRuntime:
    bind_host: str
    port: int
    root_folder: str
    username: str
    read_only: bool
    host_key_fingerprint: str = ""
    session_count: int = 0

    def to_dict(self) -> dict:
        return {
            "bind_host": self.bind_host,
            "port": self.port,
            "root_folder": self.root_folder,
            "username": self.username,
            "read_only": self.read_only,
            "host_key_fingerprint": self.host_key_fingerprint,
            "session_count": self.session_count,
        }
