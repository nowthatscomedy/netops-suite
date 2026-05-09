from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from app.models.ftp_models import FtpProfile
from app.models.profile_models import IPProfile
from app.models.scp_models import ScpProfile
from app.services.arp_scan_service import ArpScanService
from app.services.dns_service import DnsService
from app.services.ftp_client_service import FtpClientService
from app.services.ftp_server_service import FtpServerService
from app.services.iperf_service import IperfService
from app.services.logging_service import configure_logging
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
    normalize_update_config,
    save_json,
)


class AppState(QObject):
    log_message = Signal(str)
    config_reloaded = Signal()

    def __init__(self, root_dir: Path | None = None) -> None:
        super().__init__()
        self.paths: AppPaths = build_app_paths(root_dir)
        ensure_runtime_files(self.paths)

        self.logger: logging.Logger = configure_logging(self.paths.app_log, self._emit_log_message)
        self.thread_pool = QThreadPool.globalInstance()
        self.is_admin = is_running_as_admin()

        self.app_config: dict = {}
        self.ip_profiles: list[IPProfile] = []
        self.ftp_profiles: list[FtpProfile] = []
        self.ftp_runtime: dict = {}
        self.scp_profiles: list[ScpProfile] = []
        self.scp_runtime: dict = {}
        self.tftp_runtime: dict = {}
        self.reload_config_files()

        self.powershell_service = PowerShellService(self.logger)
        self.network_interface_service = NetworkInterfaceService(self.powershell_service, self.logger)
        self.oui_service = OuiService(self.paths, self.logger)
        self.arp_scan_service = ArpScanService(self.oui_service, self.logger)
        self.ping_service = PingService(self.logger)
        self.tcp_check_service = TcpCheckService(self.logger)
        self.dns_service = DnsService(self.powershell_service, self.logger)
        self.public_ip_service = PublicIpService(self.logger)
        self.trace_service = TraceService(self.logger)
        self.wireless_service = WirelessService(self.powershell_service, self.logger, self.oui_service)
        self.ftp_client_service = FtpClientService(self.paths, self.logger)
        self.ftp_server_service = FtpServerService(self.paths, self.logger)
        self.scp_client_service = ScpClientService(self.paths, self.logger)
        self.scp_server_service = ScpServerService(self.paths, self.logger)
        self.tftp_service = TftpService(self.paths, self.logger)
        self.iperf_service = IperfService(self.paths, self.logger)
        self.public_iperf_service = PublicIperfService(self.paths, self.logger)
        self.update_service = UpdateService(self.logger)

    def _emit_log_message(self, message: str) -> None:
        self.log_message.emit(message)

    def reload_config_files(self) -> None:
        loaded_config = load_json(self.paths.app_config, {})
        base_config = default_app_config()
        should_save_app_config = False
        if isinstance(loaded_config, dict):
            base_config.update({key: value for key, value in loaded_config.items() if key != "update"})
            normalized_update = normalize_update_config(loaded_config.get("update", {}))
            base_config["update"] = normalized_update

            loaded_update = loaded_config.get("update", {})
            if not isinstance(loaded_update, dict) or loaded_update != normalized_update:
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
                self.logger.info("Normalized app_config.json update channel settings.")
            if migrated_legacy:
                self.logger.info("Migrated legacy vendor presets into ip_profiles.json")
            self.logger.info("Configuration reloaded from disk.")

    def save_app_config(self, config: dict) -> None:
        normalized = dict(config)
        normalized["update"] = normalize_update_config(config.get("update", {}))
        self.app_config = normalized
        save_json(self.paths.app_config, self.app_config)
        self.logger.info("Saved app_config.json")

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

