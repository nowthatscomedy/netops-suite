from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size_bytes: int = 0
    digest_sha256: str = ""


@dataclass(slots=True)
class UpdateCheckResult:
    success: bool
    current_version: str
    latest_version: str = ""
    is_prerelease: bool = False
    update_available: bool = False
    install_ready: bool = False
    requires_config: bool = False
    message: str = ""
    details: str = ""
    release_name: str = ""
    release_url: str = ""
    published_at: str = ""
    body: str = ""
    asset: ReleaseAsset | None = None
    checksum_asset: ReleaseAsset | None = None
    verification_source: str = ""


@dataclass(slots=True)
class DownloadedUpdate:
    version: str
    asset_name: str
    asset_path: Path
    checksum_path: Path | None
    sha256: str
    verification_source: str = ""
