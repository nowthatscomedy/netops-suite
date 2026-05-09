"""
Network Device Inspection Tool - Handreamnet Module

Handreamnet 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: hn, sg
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import paramiko

from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)


# Handreamnet 장비 점검 명령어 정의
HANDREAMNET_INSPECTION_COMMANDS = {
    "handreamnet": {
        "hn": [
            "show running-config | include hostname",
            "show system fan",
            "show system temperature",
            "show system system-info",
            "show system cpu-load",
            "show system memory",
        ],
        "sg": [
            "show running-config | include hostname",
            "show system fan",
            "show system temperature",
            "show system system-info",
            "show system uptime",
            "show system cpu-load",
            "show system memory",
        ],
    }
}

# Handreamnet 장비 설정 백업 명령어 정의
HANDREAMNET_BACKUP_COMMANDS = {
    "handreamnet": {
        "hn": "show running-config",
        "sg": "show running-config",
    }
}

# Handreamnet 장비 출력 파싱 규칙
HANDREAMNET_PARSING_RULES = {
    "handreamnet": {
        "hn": {
            "show running-config | include hostname": {
                "pattern": r"hostname\s+(\S+)",
                "output_column": "Hostname",
                "first_match_only": True,
            },
            "show system fan": {
                "pattern": r"Fan Status\s*:\s*(.+)",
                "output_column": "Fan Status",
                "first_match_only": True,
            },
            "show system temperature": {
                "pattern": r"M/B\s+Temp\s*:\s*(.+)",
                "output_column": "System Temperature",
                "first_match_only": True,
            },
            "show system system-info": {
                "patterns": [
                    {
                        "pattern": r"Model\s*:\s*(\S+)",
                        "output_column": "Model",
                        "first_match_only": True,
                    },
                    {
                        "pattern": r"Serial No\s*:\s*(\S+)",
                        "output_column": "Serial Number",
                        "first_match_only": True,
                    },
                    {
                        "pattern": r"OS Version\s*:\s*(\S+)",
                        "output_column": "Version",
                        "first_match_only": True,
                    },
                    {
                        "pattern": r"Accumulation Time\s*:\s*(.+)",
                        "output_column": "Uptime",
                        "first_match_only": True,
                    },
                ],
            },
            "show system cpu-load": {
                "pattern": r"5 sec\s*:\s*([\d\.]+\s*%)",
                "output_column": "CPU Usage",
                "first_match_only": True,
            },
            "show system memory": {
                "pattern": r"Current memory usage\s*:\s*([\d\.]+\s*%)",
                "output_column": "Memory Usage",
                "first_match_only": True,
            },
        },
        "sg": {
            "show running-config | include hostname": {
                "pattern": r"hostname\s+(\S+)",
                "output_column": "Hostname",
                "first_match_only": True,
            },
            "show system fan": {
                "pattern": r"Status\s*:\s*(\w+)",
                "output_column": "Fan Status",
                "first_match_only": True,
            },
            "show system temperature": {
                "pattern": r"M/B\s+Temp\s*:\s*(.+)",
                "output_column": "System Temperature",
                "first_match_only": True,
            },
            "show system system-info": {
                "patterns": [
                    {
                        "pattern": r"Model\s*:\s*(\S+)",
                        "output_column": "Model",
                        "first_match_only": True,
                    },
                    {
                        "pattern": r"Serial No\s*:\s*(\S+)",
                        "output_column": "Serial Number",
                        "first_match_only": True,
                    },
                    {
                        "pattern": r"OS Version\s*:\s*(\S+)",
                        "output_column": "Version",
                        "first_match_only": True,
                    },
                ],
            },
            "show system uptime": {
                "pattern": r"up\s+(.+?),",
                "output_column": "Uptime",
                "first_match_only": True,
            },
            "show system cpu-load": {
                "pattern": r"5 sec\s*:\s*([\d\.]+\s*%)",
                "output_column": "CPU Usage",
                "first_match_only": True,
            },
            "show system memory": {
                "pattern": r"Used\s*:\s*(\d+)\s*kB",
                "output_column": "Memory Usage",
                "first_match_only": True,
            },
        },
    }
}


@register_handler("handreamnet", "hn", "ssh")
class HandreamnetHnSSHHandler(CustomDeviceHandler):
    """Handreamnet HN SSH 장비 핸들러 (Paramiko 기반)"""

    def __init__(self, device, timeout: int = 30, session_log_file: Optional[str] = None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt = None

    def connect(self) -> bool:
        """SSH로 장비에 연결"""
        if self.device["connection_type"].lower() != "ssh":
            raise ValueError("HandreamnetHnSSHHandler는 SSH 연결만 지원합니다")

        self.logger.debug("Handreamnet HN 장비 SSH 접속 시작: %s", self.device.get("ip"))

        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.device["ip"],
                username=self.device["username"],
                password=self.device["password"],
                port=int(self.device["port"]),
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            self.channel = self.ssh.invoke_shell(width=200, height=1000)
            self.channel.settimeout(self.timeout)

            time.sleep(2)
            output = self._read_channel()
            self.log_output("초기 출력", output)

            if ">" in output or "#" in output:
                last_line = output.strip().splitlines()[-1]
                self.prompt = last_line.strip()
                self.logger.debug("Handreamnet HN 접속 성공: %s", self.prompt)
                return True

            raise ConnectionError("Handreamnet HN 접속 실패: 프롬프트를 찾을 수 없습니다.")
        except Exception as exc:
            self.logger.error("Handreamnet HN SSH 접속 실패: %s", exc)
            if self.ssh:
                self.ssh.close()
            raise

    def _read_channel(self) -> str:
        """채널에서 출력 읽기"""
        output = ""
        time.sleep(0.5)
        if self.channel and self.channel.recv_ready():
            while self.channel.recv_ready():
                output += self.channel.recv(65535).decode("utf-8", "ignore")
                time.sleep(0.1)
        return output

    def enable(self) -> None:
        """특권 모드 진입"""
        if not self.channel:
            raise ConnectionError("SSH 채널이 연결되지 않았습니다.")

        self.channel.send("\n")
        time.sleep(0.5)
        output = self._read_channel()
        last_line = output.strip().splitlines()[-1] if output.strip() else ""
        self.prompt = last_line.strip()

        if "#" not in self.prompt and ">" in self.prompt:
            self.channel.send("enable\n")
            time.sleep(1)
            output = self._read_channel()
            self.log_output("enable 명령어 후", output)

            if "Password:" in output:
                enable_password = self.device.get("enable_password", self.device["password"])
                self.channel.send(enable_password + "\n")
                time.sleep(1)
                output = self._read_channel()
                self.log_output("enable 비밀번호 입력 후", output)

        self.channel.send("terminal length 0\n")
        time.sleep(1)
        output = self._read_channel()
        self.log_output("terminal length 0 명령어 후", output)

    def send_command(self, command: str, timeout: Optional[int] = None) -> str:
        """명령어 실행"""
        if not self.channel:
            raise ConnectionError("SSH 채널이 연결되지 않았습니다.")

        if timeout is None:
            timeout = 10

        self.log_output(f"명령어 실행: {command}", "")
        self._read_channel()
        self.channel.send(command + "\n")

        full_output = ""
        time.sleep(2)
        for _ in range(50):
            output_chunk = self._read_channel()
            full_output += output_chunk
            if "--More--" in output_chunk:
                self.channel.send(" ")
                time.sleep(1)
            else:
                break

        lines = full_output.splitlines()
        if not lines:
            return ""
        if command.strip() in lines[0]:
            lines = lines[1:]
        if lines and self.prompt and self.prompt in lines[-1]:
            lines = lines[:-1]

        cleaned_output = "\n".join(lines).strip()
        self.log_output("정리된 명령어 결과", cleaned_output)
        return cleaned_output

    def disconnect(self) -> None:
        """SSH 연결 종료"""
        if self.channel:
            self.channel.close()
        if self.ssh:
            self.ssh.close()
        self.channel = None
        self.ssh = None


@register_handler("handreamnet", "sg", "ssh")
class HandreamnetSgSSHHandler(HandreamnetHnSSHHandler):
    """Handreamnet SG SSH 장비 핸들러 (Paramiko 기반, HN 핸들러 재사용)"""