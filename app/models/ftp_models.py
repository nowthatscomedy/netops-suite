from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath


def _normalize_protocol(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"ftp", "ftps", "sftp"} else "ftp"


def _normalize_remote_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "/"
    normalized = PurePosixPath(text)
    resolved = "/" + str(normalized).lstrip("/")
    return resolved.replace("//", "/") or "/"


@dataclass(slots=True)
class FtpProfile:
    name: str
    protocol: str = "ftp"
    host: str = ""
    port: int = 21
    username: str = ""
    remote_path: str = "/"
    passive_mode: bool = True
    timeout_seconds: int = 15

    @classmethod
    def from_dict(cls, data: dict) -> "FtpProfile":
        return cls(
            name=str(data.get("name", "") or ""),
            protocol=_normalize_protocol(str(data.get("protocol", "ftp") or "ftp")),
            host=str(data.get("host", "") or ""),
            port=int(data.get("port", 21) or 21),
            username=str(data.get("username", "") or ""),
            remote_path=_normalize_remote_path(str(data.get("remote_path", "/") or "/")),
            passive_mode=bool(data.get("passive_mode", True)),
            timeout_seconds=int(data.get("timeout_seconds", 15) or 15),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "remote_path": self.remote_path,
            "passive_mode": self.passive_mode,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(slots=True)
class FtpRemoteEntry:
    name: str
    entry_type: str
    size_bytes: int = 0
    modified_at: str = ""
    permissions: str = ""
    remote_path: str = ""

    @property
    def is_dir(self) -> bool:
        return self.entry_type == "dir"

    @property
    def size_text(self) -> str:
        if self.is_dir:
            return "-"
        return f"{self.size_bytes:,}"


@dataclass(slots=True)
class FtpTransferResult:
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
class FtpServerRuntime:
    protocol: str
    bind_host: str
    port: int
    root_folder: str
    username: str
    read_only: bool
    anonymous_readonly: bool
    certificate_fingerprint: str = ""
    host_key_fingerprint: str = ""
    session_count: int = 0

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "bind_host": self.bind_host,
            "port": self.port,
            "root_folder": self.root_folder,
            "username": self.username,
            "read_only": self.read_only,
            "anonymous_readonly": self.anonymous_readonly,
            "certificate_fingerprint": self.certificate_fingerprint,
            "host_key_fingerprint": self.host_key_fingerprint,
            "session_count": self.session_count,
        }

