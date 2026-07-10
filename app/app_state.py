from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal

from app.models.ai_models import normalize_ai_chat_config
from app.models.ftp_models import FtpProfile
from app.models.profile_models import IPProfile
from app.models.scp_models import ScpProfile
from app.services.ai_model_catalog_service import AiModelCatalogService
from app.services.arp_scan_service import ArpScanService
from app.services.dns_service import DnsService
from app.services.ftp_client_service import FtpClientService
from app.services.ftp_server_service import FtpServerService
from app.services.iperf_service import IperfService
from app.services.logging_service import configure_logging, shutdown_logging
from app.services.network_interface_service import NetworkInterfaceService
from app.services.oui_service import OuiService
from app.services.ping_service import PingService
from app.services.powershell_service import PowerShellService
from app.services.public_ip_service import PublicIpService
from app.services.public_iperf_service import PublicIperfService
from app.services.scp_client_service import ScpClientService
from app.services.scp_server_service import ScpServerService
from app.services.tcp_check_service import TcpCheckService
from app.services.tftp_service import TftpService
from app.services.trace_service import TraceService
from app.services.update_service import UpdateService
from app.services.wireless_service import WirelessService
from app.utils.admin import is_running_as_admin
from app.utils.file_utils import (
    AppPaths,
    build_app_paths,
    default_app_config,
    ensure_runtime_files,
    load_json,
    migrate_config_directory,
    normalize_update_config,
    resolve_app_paths_with_settings,
    save_json,
    validate_path_settings,
)


class AppState(QObject):
    log_message = Signal(str)
    config_reloaded = Signal()
    admin_status_changed = Signal(bool)
    paths_changed = Signal()

    def __init__(
        self,
        root_dir: Path | None = None,
        startup_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__()
        report = startup_callback or (lambda _message, _detail="": None)
        report("데이터 저장 위치 확인", "설정, 로그, 내보내기 폴더 경로를 계산합니다.")
        self.paths: AppPaths = build_app_paths(root_dir)
        report("기본 파일 준비", "필수 설정 파일과 런타임 폴더를 확인합니다.")
        ensure_runtime_files(self.paths)

        report("로깅 준비", "앱 로그 파일과 화면 로그 전달자를 연결합니다.")
        self.logger: logging.Logger = configure_logging(self.paths.app_log, self._emit_log_message)
        self.thread_pool = QThreadPool.globalInstance()
        self._is_admin = False
        report("권한 상태 확인", "관리자 권한 실행 여부를 확인합니다.")
        self.is_admin = is_running_as_admin()

        self.app_config: dict = {}
        self.ip_profiles: list[IPProfile] = []
        self.ftp_profiles: list[FtpProfile] = []
        self.ftp_runtime: dict = {}
        self.scp_profiles: list[ScpProfile] = []
        self.scp_runtime: dict = {}
        self.tftp_runtime: dict = {}
        report("설정 파일 읽기", "프로파일, 전송 설정, UI 상태를 불러옵니다.")
        self.reload_config_files()

        report("네트워크 명령 서비스 준비", "PowerShell 기반 네트워크 작업 서비스를 초기화합니다.")
        self.powershell_service = PowerShellService(self.logger)
        self.network_interface_service = NetworkInterfaceService(self.powershell_service, self.logger)
        report("진단 서비스 준비", "Ping, TCP, DNS, 경로 추적 기능을 준비합니다.")
        self.oui_service = OuiService(self.paths, self.logger)
        self.arp_scan_service = ArpScanService(self.oui_service, self.logger)
        self.ping_service = PingService(self.logger)
        self.tcp_check_service = TcpCheckService(self.logger)
        self.dns_service = DnsService(self.powershell_service, self.logger)
        self.public_ip_service = PublicIpService(self.logger)
        self.trace_service = TraceService(self.logger)
        self.wireless_service = WirelessService(self.powershell_service, self.logger, self.oui_service)
        self.ai_model_catalog_service = AiModelCatalogService(self.paths.ai_model_catalog_cache)
        report("파일 전송 서비스 준비", "FTP, SCP, TFTP, iperf 관련 런타임을 초기화합니다.")
        self.ftp_client_service = FtpClientService(self.paths, self.logger)
        self.ftp_server_service = FtpServerService(self.paths, self.logger)
        self.scp_client_service = ScpClientService(self.paths, self.logger)
        self.scp_server_service = ScpServerService(self.paths, self.logger)
        self.tftp_service = TftpService(self.paths, self.logger)
        self.iperf_service = IperfService(self.paths, self.logger)
        self.public_iperf_service = PublicIperfService(self.paths, self.logger)
        report("업데이트 서비스 준비", "릴리스 확인과 설치 파일 검증 기능을 준비합니다.")
        self.update_service = UpdateService(self.logger)

    @property
    def is_admin(self) -> bool:
        return self._is_admin

    @is_admin.setter
    def is_admin(self, value: bool) -> None:
        normalized = bool(value)
        if self._is_admin == normalized:
            return
        self._is_admin = normalized
        self.admin_status_changed.emit(normalized)

    def _emit_log_message(self, message: str) -> None:
        self.log_message.emit(message)

    def reload_config_files(self) -> None:
        loaded_config = load_json(self.paths.app_config, {})
        base_config = default_app_config()
        should_save_app_config = False
        if isinstance(loaded_config, dict):
            base_config.update({key: value for key, value in loaded_config.items() if key not in {"update", "ai_chat"}})
            normalized_update = normalize_update_config(loaded_config.get("update", {}))
            base_config["update"] = normalized_update
            normalized_ai_chat = normalize_ai_chat_config(loaded_config.get("ai_chat", {}))
            base_config["ai_chat"] = normalized_ai_chat

            loaded_update = loaded_config.get("update", {})
            if not isinstance(loaded_update, dict) or loaded_update != normalized_update:
                should_save_app_config = True
            loaded_ai_chat = loaded_config.get("ai_chat", {})
            if not isinstance(loaded_ai_chat, dict) or loaded_ai_chat != normalized_ai_chat:
                should_save_app_config = True
        else:
            should_save_app_config = True
        self.app_config = base_config
        if should_save_app_config:
            save_json(self.paths.app_config, self.app_config)
        profiles = [IPProfile.from_dict(item) for item in load_json(self.paths.ip_profiles, [])]
        legacy_presets = load_json(self.paths.vendor_presets, [])
        migrated_legacy = bool(legacy_presets)
        existing_names = {profile.name.casefold() for profile in profiles if profile.name}
        for item in legacy_presets:
            migrated = IPProfile.from_vendor_preset_dict(item)
            if migrated.name and migrated.name.casefold() not in existing_names:
                profiles.append(migrated)
                existing_names.add(migrated.name.casefold())
        self.ip_profiles = profiles
        self.ftp_profiles = [FtpProfile.from_dict(item) for item in load_json(self.paths.ftp_profiles, [])]
        loaded_ftp_runtime = load_json(self.paths.ftp_runtime, {})
        self.ftp_runtime = loaded_ftp_runtime if isinstance(loaded_ftp_runtime, dict) else {}
        self.scp_profiles = [ScpProfile.from_dict(item) for item in load_json(self.paths.scp_profiles, [])]
        loaded_scp_runtime = load_json(self.paths.scp_runtime, {})
        self.scp_runtime = loaded_scp_runtime if isinstance(loaded_scp_runtime, dict) else {}
        loaded_tftp_runtime = load_json(self.paths.tftp_runtime, {})
        self.tftp_runtime = loaded_tftp_runtime if isinstance(loaded_tftp_runtime, dict) else {}
        if migrated_legacy:
            save_json(self.paths.ip_profiles, [profile.to_dict() for profile in self.ip_profiles])
            save_json(self.paths.vendor_presets, [])
        self.config_reloaded.emit()
        if hasattr(self, "logger"):
            if should_save_app_config:
                self.logger.info("Normalized app_config.json update settings.")
            if migrated_legacy:
                self.logger.info("Migrated legacy vendor presets into ip_profiles.json")
            self.logger.info("Configuration reloaded from disk.")

    def save_app_config(self, config: dict) -> None:
        normalized = dict(config)
        normalized["update"] = normalize_update_config(config.get("update", {}))
        normalized["ai_chat"] = normalize_ai_chat_config(config.get("ai_chat", {}))
        self.app_config = normalized
        save_json(self.paths.app_config, self.app_config)
        self.logger.info("Saved app_config.json")

    def save_path_settings(self, settings: dict) -> dict:
        normalized_settings = validate_path_settings(settings)
        target_paths = resolve_app_paths_with_settings(self.paths, normalized_settings)

        current_config_dir = Path(self.paths.config_dir).resolve(strict=False)
        target_config_dir = Path(target_paths.config_dir).resolve(strict=False)
        current_logs_dir = Path(self.paths.logs_dir).resolve(strict=False)
        target_logs_dir = Path(target_paths.logs_dir).resolve(strict=False)
        config_changed = current_config_dir != target_config_dir
        logs_changed = current_logs_dir != target_logs_dir

        copied_files: tuple[Path, ...] = ()
        skipped_files: tuple[Path, ...] = ()
        if config_changed:
            copied_files, skipped_files = migrate_config_directory(current_config_dir, target_config_dir)

        ensure_runtime_files(target_paths)
        if self.paths.path_settings is None:
            raise RuntimeError("Path settings file is not configured.")
        save_json(self.paths.path_settings, normalized_settings)

        if config_changed:
            for attribute in (
                "config_dir",
                "app_config",
                "ip_profiles",
                "ftp_profiles",
                "ftp_runtime",
                "scp_profiles",
                "scp_runtime",
                "tftp_runtime",
                "vendor_presets",
                "public_iperf_cache",
                "ai_model_catalog_cache",
                "oui_cache",
                "ftp_keys_dir",
            ):
                setattr(self.paths, attribute, getattr(target_paths, attribute))
        self.paths.exports_dir = target_paths.exports_dir
        restart_required = config_changed or logs_changed
        result = {
            "target_paths": target_paths,
            "copied_files": copied_files,
            "skipped_files": skipped_files,
            "restart_required": restart_required,
        }
        self.logger.info(
            "Saved path settings (config=%s, logs=%s, exports=%s, copied=%s, skipped=%s, restart_required=%s).",
            target_paths.config_dir,
            target_paths.logs_dir,
            target_paths.exports_dir,
            len(copied_files),
            len(skipped_files),
            restart_required,
        )
        self.paths_changed.emit()
        return result

    def get_ui_state(self) -> dict:
        ui_state = self.app_config.get("ui_state", {})
        return dict(ui_state) if isinstance(ui_state, dict) else {}

    def save_ip_profiles(self, profiles: list[IPProfile]) -> None:
        self.ip_profiles = profiles
        save_json(self.paths.ip_profiles, [profile.to_dict() for profile in self.ip_profiles])
        self.logger.info("Saved %s IP profiles.", len(self.ip_profiles))
        self.config_reloaded.emit()

    def save_ftp_profiles(self, profiles: list[FtpProfile]) -> None:
        self.ftp_profiles = profiles
        save_json(self.paths.ftp_profiles, [profile.to_dict() for profile in self.ftp_profiles])
        self.logger.info("Saved %s FTP profiles.", len(self.ftp_profiles))
        self.config_reloaded.emit()

    def save_ftp_runtime(self, runtime: dict) -> None:
        self.ftp_runtime = dict(runtime)
        save_json(self.paths.ftp_runtime, self.ftp_runtime)
        self.logger.info("Saved ftp_runtime.json")

    def save_scp_profiles(self, profiles: list[ScpProfile]) -> None:
        self.scp_profiles = profiles
        save_json(self.paths.scp_profiles, [profile.to_dict() for profile in self.scp_profiles])
        self.logger.info("Saved %s SCP profiles.", len(self.scp_profiles))
        self.config_reloaded.emit()

    def save_scp_runtime(self, runtime: dict) -> None:
        self.scp_runtime = dict(runtime)
        save_json(self.paths.scp_runtime, self.scp_runtime)
        self.logger.info("Saved scp_runtime.json")

    def save_tftp_runtime(self, runtime: dict) -> None:
        self.tftp_runtime = dict(runtime)
        save_json(self.paths.tftp_runtime, self.tftp_runtime)
        self.logger.info("Saved tftp_runtime.json")

    def shutdown(self) -> None:
        shutdown_logging(self.logger)

