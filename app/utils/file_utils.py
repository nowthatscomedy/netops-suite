from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.ai_models import default_ai_chat_config

DEFAULT_UPDATE_REPO = "nowthatscomedy/netops-suite"
DEFAULT_UPDATE_ASSET_PATTERN = (
    r"^NetOpsSuite-setup-\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\.exe$"
)
PROJECT_DATA_ENV = "NETOPS_SUITE_USE_PROJECT_DATA"
DATA_ROOT_ENV = "NETOPS_SUITE_DATA_ROOT"
PATH_SETTINGS_FILENAME = "path_settings.json"
PATH_SETTINGS_VERSION = 1
PATH_SETTING_KEYS = ("config_dir", "logs_dir", "exports_dir")

LOGGER = logging.getLogger("netops_suite.file_utils")
_SAVE_JSON_LOCK = threading.RLock()


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
    ai_model_catalog_cache: Path
    oui_cache: Path
    ftp_keys_dir: Path
    app_log: Path
    path_settings: Path | None = None


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


def _path_or_existing_parent_is_symlink(path: Path) -> bool:
    absolute = path.absolute()
    return any(candidate.is_symlink() for candidate in (absolute, *absolute.parents))


def _is_protected_install_root(root: Path) -> bool:
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base_dir = os.environ.get(env_name)
        if base_dir and _is_relative_to(root, Path(base_dir)):
            return True
    return False


def _is_writable_directory(path: Path) -> bool:
    descriptor: int | None = None
    probe_path: Path | None = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".netops_write_test_", dir=path
        )
        probe_path = Path(probe_name)
        os.close(descriptor)
        descriptor = None
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def detect_data_root(root: Path, *, prefer_project_data: bool = False) -> Path:
    override = os.environ.get(DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()

    if (
        prefer_project_data or _env_flag_enabled(PROJECT_DATA_ENV)
    ) and not _is_protected_install_root(root):
        if _is_writable_directory(root):
            return root

    return default_data_root()


def default_path_settings() -> dict[str, Any]:
    return {
        "version": PATH_SETTINGS_VERSION,
        "config_dir": "",
        "logs_dir": "",
        "exports_dir": "",
    }


def _contains_control_character(value: str) -> bool:
    return any(
        ord(character) < 32 or 127 <= ord(character) <= 159 for character in value
    )


def normalize_path_settings(settings: Any) -> dict[str, Any]:
    normalized = default_path_settings()
    if not isinstance(settings, dict):
        return normalized
    if settings.get("version", PATH_SETTINGS_VERSION) != PATH_SETTINGS_VERSION:
        return normalized
    for key in PATH_SETTING_KEYS:
        value = settings.get(key, "")
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if isinstance(value, str):
            normalized[key] = (
                value if _contains_control_character(value) else value.strip()
            )
    return normalized


def _absolute_path_text(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def effective_path_settings(paths: AppPaths) -> dict[str, Any]:
    return {
        "version": PATH_SETTINGS_VERSION,
        "config_dir": _absolute_path_text(paths.config_dir),
        "logs_dir": _absolute_path_text(paths.logs_dir),
        "exports_dir": _absolute_path_text(paths.exports_dir),
    }


def default_effective_path_settings(paths: AppPaths) -> dict[str, Any]:
    data_root = Path(paths.data_root)
    logs_dir = data_root / "logs"
    return {
        "version": PATH_SETTINGS_VERSION,
        "config_dir": _absolute_path_text(data_root / "config"),
        "logs_dir": _absolute_path_text(logs_dir),
        "exports_dir": _absolute_path_text(logs_dir / "exports"),
    }


def validate_path_settings(settings: Any) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise ValueError("경로 설정은 object 형식이어야 합니다.")
    if settings.get("version", PATH_SETTINGS_VERSION) != PATH_SETTINGS_VERSION:
        raise ValueError("지원하지 않는 경로 설정 버전입니다.")

    validated = default_path_settings()
    for key in PATH_SETTING_KEYS:
        raw_value = settings.get(key, "")
        if isinstance(raw_value, os.PathLike):
            raw_value = os.fspath(raw_value)
        if not isinstance(raw_value, str):
            raise ValueError(f"{key} 경로는 문자열이어야 합니다.")
        if _contains_control_character(raw_value):
            raise ValueError(f"{key} 경로에 제어문자를 사용할 수 없습니다.")

        text = raw_value.strip()
        if not text:
            continue
        expanded = os.path.expandvars(os.path.expanduser(text))
        candidate = Path(expanded)
        if not candidate.is_absolute():
            raise ValueError(f"{key} 경로는 절대경로여야 합니다.")
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise ValueError(f"{key} 경로를 확인할 수 없습니다: {exc}") from exc
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(f"{key} 경로가 폴더가 아닙니다: {resolved}")
        if not _is_writable_directory(resolved):
            raise ValueError(f"{key} 경로에 쓸 수 없습니다: {resolved}")
        validated[key] = str(resolved)
    return validated


def resolve_app_paths_with_settings(current_paths: AppPaths, settings: Any) -> AppPaths:
    validated = validate_path_settings(settings)
    defaults = default_effective_path_settings(current_paths)
    config_dir = Path(validated["config_dir"] or defaults["config_dir"])
    logs_dir = Path(validated["logs_dir"] or defaults["logs_dir"])
    exports_dir = (
        Path(validated["exports_dir"])
        if validated["exports_dir"]
        else logs_dir / "exports"
    )
    path_settings = current_paths.path_settings or (
        Path(current_paths.data_root) / PATH_SETTINGS_FILENAME
    )

    return AppPaths(
        root=Path(current_paths.root),
        data_root=Path(current_paths.data_root),
        config_dir=config_dir,
        logs_dir=logs_dir,
        exports_dir=exports_dir,
        app_config=config_dir / Path(current_paths.app_config).name,
        ip_profiles=config_dir / Path(current_paths.ip_profiles).name,
        ftp_profiles=config_dir / Path(current_paths.ftp_profiles).name,
        ftp_runtime=config_dir / Path(current_paths.ftp_runtime).name,
        scp_profiles=config_dir / Path(current_paths.scp_profiles).name,
        scp_runtime=config_dir / Path(current_paths.scp_runtime).name,
        tftp_runtime=config_dir / Path(current_paths.tftp_runtime).name,
        vendor_presets=config_dir / Path(current_paths.vendor_presets).name,
        public_iperf_cache=config_dir / Path(current_paths.public_iperf_cache).name,
        ai_model_catalog_cache=config_dir
        / Path(current_paths.ai_model_catalog_cache).name,
        oui_cache=config_dir / Path(current_paths.oui_cache).name,
        ftp_keys_dir=config_dir / Path(current_paths.ftp_keys_dir).name,
        app_log=logs_dir / Path(current_paths.app_log).name,
        path_settings=Path(path_settings),
    )


def migrate_config_directory(
    source: str | Path,
    target: str | Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    source_input = Path(source).expanduser()
    target_input = Path(target).expanduser()
    if _path_or_existing_parent_is_symlink(source_input):
        raise ValueError("설정 원본 경로와 상위 경로는 심볼릭 링크일 수 없습니다.")
    if _path_or_existing_parent_is_symlink(target_input):
        raise ValueError("설정 대상 경로와 상위 경로는 심볼릭 링크일 수 없습니다.")

    source_dir = source_input.resolve(strict=False)
    target_dir = target_input.resolve(strict=False)
    if not source_dir.exists():
        return (), ()
    if not source_dir.is_dir():
        raise ValueError(f"설정 원본 경로가 폴더가 아닙니다: {source_dir}")
    if target_dir.exists() and not target_dir.is_dir():
        raise ValueError(f"설정 대상 경로가 폴더가 아닙니다: {target_dir}")
    if source_dir == target_dir:
        return (), ()
    if _is_relative_to(target_dir, source_dir):
        raise ValueError("설정 대상 경로는 원본 경로 내부일 수 없습니다.")

    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    skipped: list[Path] = []
    for source_path in sorted(source_dir.rglob("*")):
        relative_path = source_path.relative_to(source_dir)
        if source_path.is_symlink():
            skipped.append(source_path)
            continue
        if any(
            (source_dir.joinpath(*relative_path.parts[:index])).is_symlink()
            for index in range(1, len(relative_path.parts))
        ):
            skipped.append(source_path)
            continue
        if not source_path.is_file():
            continue

        target_path = target_dir / relative_path
        target_ancestors = tuple(
            target_dir.joinpath(*relative_path.parts[:index])
            for index in range(1, len(relative_path.parts))
        )
        if any(
            ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir())
            for ancestor in target_ancestors
        ):
            skipped.append(source_path)
            continue
        if target_path.exists() or target_path.is_symlink():
            skipped.append(source_path)
            continue
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            skipped.append(source_path)
            continue
        if any(ancestor.is_symlink() for ancestor in target_ancestors):
            skipped.append(source_path)
            continue
        created = False
        try:
            with (
                source_path.open("rb") as source_handle,
                target_path.open("xb") as target_handle,
            ):
                created = True
                shutil.copyfileobj(source_handle, target_handle)
            shutil.copystat(source_path, target_path)
        except FileExistsError:
            skipped.append(source_path)
            continue
        except Exception:
            if created:
                target_path.unlink(missing_ok=True)
            raise
        copied.append(target_path)
    return tuple(copied), tuple(skipped)


def build_app_paths(root_dir: Path | None = None) -> AppPaths:
    root = Path(root_dir) if root_dir else detect_root_path()
    data_root = detect_data_root(root, prefer_project_data=root_dir is not None)
    config_dir = data_root / "config"
    logs_dir = data_root / "logs"
    exports_dir = logs_dir / "exports"
    base_paths = AppPaths(
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
        ai_model_catalog_cache=config_dir / "ai_model_catalog_cache.json",
        oui_cache=config_dir / "oui_cache.json",
        ftp_keys_dir=config_dir / "ftp_keys",
        app_log=logs_dir / "app.log",
        path_settings=data_root / PATH_SETTINGS_FILENAME,
    )
    raw_settings = load_json(base_paths.path_settings, default_path_settings())
    try:
        return resolve_app_paths_with_settings(base_paths, raw_settings)
    except ValueError as exc:
        LOGGER.warning(
            "Invalid path settings in %s; using defaults: %s",
            base_paths.path_settings,
            exc,
        )
        return base_paths


def default_app_config() -> dict[str, Any]:
    return {
        "app_name": "NetOps Suite",
        "wireless_refresh_interval_sec": 2,
        "default_nslookup_type": "A",
        "update": default_update_config(),
        "ai_chat": default_ai_chat_config(),
    }


def default_update_config() -> dict[str, Any]:
    return {
        "check_on_startup": False,
    }


def normalize_update_config(update_config: Any) -> dict[str, Any]:
    config = default_update_config()
    if isinstance(update_config, dict):
        config["check_on_startup"] = bool(
            update_config.get("check_on_startup", config["check_on_startup"])
        )
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
    for directory in (
        paths.config_dir,
        paths.logs_dir,
        paths.exports_dir,
        paths.ftp_keys_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    if paths.path_settings is not None and not paths.path_settings.exists():
        save_json(paths.path_settings, default_path_settings())

    defaults = {
        paths.app_config: (
            default_app_config(),
            paths.root / "config" / "app_config.json",
        ),
        paths.ip_profiles: (
            default_ip_profiles(),
            paths.root / "config" / "ip_profiles.json",
        ),
        paths.ftp_profiles: (
            default_ftp_profiles(),
            paths.root / "config" / "ftp_profiles.json",
        ),
        paths.ftp_runtime: (
            default_ftp_runtime(),
            paths.root / "config" / "ftp_runtime.json",
        ),
        paths.scp_profiles: (
            default_scp_profiles(),
            paths.root / "config" / "scp_profiles.json",
        ),
        paths.scp_runtime: (
            default_scp_runtime(),
            paths.root / "config" / "scp_runtime.json",
        ),
        paths.tftp_runtime: (
            default_tftp_runtime(),
            paths.root / "config" / "tftp_runtime.json",
        ),
        paths.vendor_presets: (
            default_vendor_presets(),
            paths.root / "config" / "vendor_presets.json",
        ),
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
    except (json.JSONDecodeError, UnicodeError) as exc:
        backup_path = _backup_invalid_json(file_path)
        if backup_path:
            LOGGER.warning(
                "Invalid JSON in %s. Backed up to %s: %s", file_path, backup_path, exc
            )
        else:
            LOGGER.warning("Invalid JSON in %s and backup failed: %s", file_path, exc)
        return default
    except OSError as exc:
        LOGGER.warning("Failed to read JSON from %s: %s", file_path, exc)
        return default


def save_json(file_path: Path, data: Any) -> None:
    with _SAVE_JSON_LOCK:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            dir=file_path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(payload)
            temp_path.replace(file_path)
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    LOGGER.warning("Failed to remove temporary JSON file %s", temp_path)


def _backup_invalid_json(file_path: Path) -> Path | None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.with_name(f"{file_path.name}.invalid-{timestamp}")
    try:
        shutil.copyfile(file_path, backup_path)
        return backup_path
    except OSError:
        return None


def timestamped_export_path(directory: Path, prefix: str, extension: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = extension.lstrip(".")
    candidate = directory / f"{prefix}_{timestamp}.{suffix}"
    if not candidate.exists():
        return candidate
    for index in range(1, 10000):
        candidate = directory / f"{prefix}_{timestamp}_{index:02d}.{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(
        f"사용 가능한 내보내기 파일 이름을 만들 수 없습니다: {directory}"
    )


def open_in_explorer(path: Path) -> None:
    os.startfile(str(path))
