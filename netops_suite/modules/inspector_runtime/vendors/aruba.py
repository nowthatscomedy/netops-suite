"""
Network Device Inspection Tool - Aruba Module

Aruba 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: aruba_os
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from netmiko import ConnectHandler
from netmiko.base_connection import BaseConnection

from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)


def parsing_aruba_hostname(output: str) -> str:
    """Aruba running-config에서 호스트네임 파싱"""
    match = re.search(r'hostname\s+"(\S+)"', output)
    return match.group(1) if match else ""


def parsing_aruba_fan_status(output: str) -> str:
    """Aruba 팬 상태 파싱"""
    match = re.search(r"(\d+\s+/\s+\d+)\s+Fans\s+in\s+Failure\s+State", output)
    return match.group(1) if match else ""


def parsing_aruba_temperature(output: str) -> str:
    """Aruba 온도 파싱"""
    match = re.search(r"Chassis\s+(\d+)", output)
    return match.group(1) if match else ""


def parsing_aruba_uptime(output: str) -> str:
    """Aruba 업타임 파싱"""
    match = re.search(r"Up\s+Time\s+:\s+(\d+\s+days)", output)
    return match.group(1) if match else ""


def parsing_aruba_cpu_usage(output: str) -> str:
    """Aruba CPU 사용량 파싱"""
    match = re.search(r"CPU\s+Util\s+\(%\)\s+:\s+(\d+)", output)
    return match.group(1) if match else ""


def parsing_aruba_memory_usage(output: str) -> str:
    """Aruba 메모리 사용량 파싱"""
    match = re.search(r"CPU\s+Util\s+\(%\)\s+:\s+\d+\s+Free\s+:\s+(.*)", output)
    return match.group(1).strip() if match else ""


ARUBA_INSPECTION_COMMANDS = {
    "aruba": {
        "aruba_os": [
            "show running-config",
            "show system information",
            "show system fan",
            "show system temperature",
        ]
    }
}

ARUBA_BACKUP_COMMANDS = {
    "aruba": {
        "aruba_os": "show running-config",
    }
}

ARUBA_PARSING_RULES = {
    "aruba": {
        "aruba_os": {
            "show running-config": {
                "patterns": [
                    {
                        "custom_parser": "parsing_aruba_hostname",
                        "output_column": "Hostname",
                    },
                    {
                        "pattern": r";\s*(\w+)\s+Configuration Editor",
                        "output_column": "Model",
                        "first_match_only": True,
                    },
                ],
            },
            "show system information": {
                "patterns": [
                    {
                        "pattern": r"Software revision\s*:\s*(\S+)",
                        "output_column": "Version",
                        "first_match_only": True,
                    },
                    {
                        "pattern": r"Serial Number\s*:\s*(\S+)",
                        "output_column": "Serial Number",
                        "first_match_only": True,
                    },
                    {
                        "custom_parser": "parsing_aruba_uptime",
                        "output_column": "Uptime",
                    },
                    {
                        "custom_parser": "parsing_aruba_cpu_usage",
                        "output_column": "CPU Usage",
                    },
                    {
                        "custom_parser": "parsing_aruba_memory_usage",
                        "output_column": "Memory Usage",
                    },
                ]
            },
            "show system fan": {
                "custom_parser": "parsing_aruba_fan_status",
                "output_column": "Fan Status",
            },
            "show system temperature": {
                "custom_parser": "parsing_aruba_temperature",
                "output_column": "System Temperature",
            },
        }
    }
}


@register_handler("aruba", "aruba_os", "ssh")
class ArubaOsSSHHandler(CustomDeviceHandler):
    """Aruba OS SSH 장비 핸들러 (Netmiko 기반)"""

    def __init__(self, device, timeout: int = 30, session_log_file: Optional[str] = None):
        super().__init__(device, timeout, session_log_file)
        self.conn: Optional[BaseConnection] = None

    def _build_params(self) -> dict:
        enable_password = self.device.get("enable_password") or self.device.get("password")
        params = {
            "device_type": "aruba_os",
            "host": str(self.device["ip"]),
            "username": str(self.device["username"]),
            "password": str(self.device["password"]),
            "port": int(self.device["port"]),
            "secret": str(enable_password or ""),
            "timeout": int(self.timeout),
            "fast_cli": False,
        }
        if self.session_log_file:
            params["session_log"] = str(self.session_log_file)
        return params

    def connect(self) -> bool:
        if self.device["connection_type"].lower() != "ssh":
            raise ValueError("ArubaOsSSHHandler는 SSH 연결만 지원합니다")
        try:
            self.conn = ConnectHandler(**self._build_params())
            self.logger.debug("Aruba OS 접속 성공: %s", self.device.get("ip"))
            return True
        except Exception as exc:
            self.logger.error("Aruba OS SSH 접속 실패: %s", exc)
            self.conn = None
            raise

    def enable(self) -> None:
        if not self.conn:
            raise ConnectionError("Netmiko 연결이 초기화되지 않았습니다.")
        self.conn.enable()
        output = self.conn.send_command_timing("no page")
        self.log_output("no page 명령어 후", output)

    def send_command(self, command: str, timeout: Optional[int] = None) -> str:
        if not self.conn:
            raise ConnectionError("Netmiko 연결이 초기화되지 않았습니다.")
        read_timeout = int(timeout or 30)
        self.log_output(f"명령어 실행: {command}", "")
        output = self.conn.send_command(command, read_timeout=read_timeout, strip_command=True, strip_prompt=True)
        self.log_output("정리된 명령어 결과", output)
        return output.strip()

    def disconnect(self) -> None:
        if self.conn:
            self.conn.disconnect()
        self.conn = None
