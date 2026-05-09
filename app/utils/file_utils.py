from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_UPDATE_REPO = "nowthatscomedy/netops-suite"
DEFAULT_UPDATE_ASSET_PATTERN = r"NetOpsSuite-setup.*\.exe$"


@dataclass(slots=True)
class AppPaths:
    root: Path
    data_root: Path
    config_dir: Path
    logs_dir: Path
    exports_dir: Path
    app_config: Path
    ip_profiles: Path
    ftp_profiles: Path
    ftp_runtime: Path
    scp_profiles: Path
    scp_runtime: Path
    tftp_runtime: Path
    vendor_presets: Path
    public_iperf_cache: Path
    oui_cache: Path
    ftp_keys_dir: Path
    app_log: Path


def detect_root_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def execution_environment_label() -> str:
    return "설치본 실행" if is_packaged_runtime() else "소스 실행"


def resolve_asset_path(*parts: str) -> Path:
    relative_path = Path("assets").joinpath(*parts)
    candidates: list[Path] = []

    if is_packaged_runtime():
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            candidates.append(Path(bundle_root) / relative_path)
        root = detect_root_path()
        candidates.extend([root / "_internal" / relative_path, root / relative_path])
    else:
        candidates.append(detect_root_path() / relative_path)

    seen: set[Path] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)

    for candidate in unique_candidates:
        if candidate.exists():
            return candidate

    return unique_candidates[0]


def default_data_root() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "NetOps Suite"
    return Path.home() / "AppData" / "Local" / "NetOps Suite"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _is_protected_install_root(root: Path) -> bool:
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base_dir = os.environ.get(env_name)
        if base_dir and _is_relative_to(root, Path(base_dir)):
            return True
    return False


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".netops_write_test_{os.getpid()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def detect_data_root(root: Path) -> Path:
    if _is_protected_install_root(root):
        return default_data_root()
    if _is_writable_directory(root):
        return root
    return default_data_root()


def build_app_paths(root_dir: Path | None = None) -> AppPaths:
    root = Path(root_dir) if root_dir else detect_root_path()
    data_root = detect_data_root(root)
    config_dir = data_root / "config"
    logs_dir = data_root / "logs"
    exports_dir = logs_dir / "exports"
    return AppPaths(
        root=root,
        data_root=data_root,
        config_dir=config_dir,
        logs_dir=logs_dir,
        exports_dir=exports_dir,
        app_config=config_dir / "app_config.json",
        ip_profiles=config_dir / "ip_profiles.json",
        ftp_profiles=config_dir / "ftp_profiles.json",
        ftp_runtime=config_dir / "ftp_runtime.json",
        scp_profiles=config_dir / "scp_profiles.json",
        scp_runtime=config_dir / "scp_runtime.json",
        tftp_runtime=config_dir / "tftp_runtime.json",
        vendor_presets=config_dir / "vendor_presets.json",
        public_iperf_cache=config_dir / "public_iperf_servers_cache.json",
        oui_cache=config_dir / "oui_cache.json",
        ftp_keys_dir=config_dir / "ftp_keys",
        app_log=logs_dir / "app.log",
    )


def default_app_config() -> dict[str, Any]:
    return {
        "app_name": "NetOps Suite",
        "default_ping_count": 4,
        "default_ping_timeout_ms": 4000,
        "default_ping_workers": 8,
        "default_tcp_timeout_ms": 1000,
        "default_tcp_workers": 32,
        "wireless_refresh_interval_sec": 2,
        "default_nslookup_type": "A",
        "update": default_update_config(),
    }


def default_update_config() -> dict[str, Any]:
    return {
        "github_repo": DEFAULT_UPDATE_REPO,
        "installer_asset_pattern": DEFAULT_UPDATE_ASSET_PATTERN,
        "check_on_startup": True,
        "include_prerelease": False,
        "release_channel": "stable",
    }


def normalize_update_config(update_config: Any) -> dict[str, Any]:
    config = default_update_config()
    if isinstance(update_config, dict):
        config["check_on_startup"] = bool(update_config.get("check_on_startup", config["check_on_startup"]))
        channel = str(update_config.get("release_channel", "") or "").strip().lower()
        if channel in {"stable", "prerelease"}:
            config["release_channel"] = channel
            config["include_prerelease"] = channel == "prerelease"
        else:
            # Migrate older configs to the safer stable channel by default.
            config["release_channel"] = "stable"
            config["include_prerelease"] = False
    return config


def default_ip_profiles() -> list[dict[str, Any]]:
    return [
        {
            "name": "DHCP Auto",
            "mode": "dhcp",
            "interface_name": "",
            "local_ip": "",
            "prefix": 24,
            "gateway": "",
            "dns": [],
            "target_vendor": "",
            "target_ip": "",
            "notes": "Reset selected adapter to DHCP and automatic DNS.",
        },
        {
            "name": "Lab Access 192.168.1.10/24",
            "mode": "static",
            "interface_name": "",
            "local_ip": "192.168.1.10",
            "prefix": 24,
            "gateway": "",
            "dns": ["8.8.8.8", "1.1.1.1"],
            "target_vendor": "Lab",
            "target_ip": "192.168.1.1",
            "notes": "Example profile for initial device access work.",
        },
    ]


def default_vendor_presets() -> list[dict[str, Any]]:
    return []


def default_ftp_profiles() -> list[dict[str, Any]]:
    return []


def default_ftp_runtime() -> dict[str, Any]:
    return {
        "client": {
            "protocol": "ftp",
            "host": "",
            "port": "21",
            "username": "",
            "passive_mode": True,
            "timeout_seconds": "15",
            "local_folder": "",
            "remote_path": "/",
            "selected_profile": "",
        },
        "server": {
            "protocol": "ftp",
            "bind_host": "0.0.0.0",
            "port": "2121",
            "root_folder": "",
            "username": "netops",
            "read_only": False,
            "anonymous_readonly": False,
        },
    }


def default_scp_profiles() -> list[dict[str, Any]]:
    return []


def default_scp_runtime() -> dict[str, Any]:
    return {
        "client": {
            "host": "",
            "port": "22",
            "username": "",
            "timeout_seconds": "15",
            "remote_path": ".",
            "remote_sources": "",
            "local_folder": "",
            "selected_profile": "",
        },
        "server": {
            "bind_host": "0.0.0.0",
            "port": "2223",
            "root_folder": "",
            "username": "netops",
            "read_only": False,
        },
    }


def default_tftp_runtime() -> dict[str, Any]:
    return {
        "client": {
            "host": "",
            "port": "69",
            "remote_path": "",
            "local_folder": "",
            "local_upload_path": "",
            "timeout_seconds": "5",
            "retries": "3",
        },
        "server": {
            "bind_host": "0.0.0.0",
            "port": "69",
            "root_folder": "",
            "read_only": True,
        },
    }


def ensure_runtime_files(paths: AppPaths) -> None:
    for directory in (paths.config_dir, paths.logs_dir, paths.exports_dir, paths.ftp_keys_dir):
        directory.mkdir(parents=True, exist_ok=True)

    defaults = {
        paths.app_config: (default_app_config(), paths.root / "config" / "app_config.json"),
        paths.ip_profiles: (default_ip_profiles(), paths.root / "config" / "ip_profiles.json"),
        paths.ftp_profiles: (default_ftp_profiles(), paths.root / "config" / "ftp_profiles.json"),
        paths.ftp_runtime: (default_ftp_runtime(), paths.root / "config" / "ftp_runtime.json"),
        paths.scp_profiles: (default_scp_profiles(), paths.root / "config" / "scp_profiles.json"),
        paths.scp_runtime: (default_scp_runtime(), paths.root / "config" / "scp_runtime.json"),
        paths.tftp_runtime: (default_tftp_runtime(), paths.root / "config" / "tftp_runtime.json"),
        paths.vendor_presets: (default_vendor_presets(), paths.root / "config" / "vendor_presets.json"),
    }
    for file_path, (default_value, source_path) in defaults.items():
        if not file_path.exists():
            if source_path.exists():
                try:
                    shutil.copyfile(source_path, file_path)
                    continue
                except OSError:
                    pass
            save_json(file_path, default_value)

    gitkeep = paths.logs_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")

    if not paths.app_log.exists():
        paths.app_log.touch()


def load_json(file_path: Path, default: Any) -> Any:
    if not file_path.exists():
        return default
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def timestamped_export_path(directory: Path, prefix: str, extension: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return directory / f"{prefix}_{timestamp}.{extension.lstrip('.')}"


def open_in_explorer(path: Path) -> None:
    os.startfile(str(path))
