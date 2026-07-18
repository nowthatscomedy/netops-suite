"""
Network Device Inspection Tool - Cisco Module

Cisco 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: ios, ios-xe, legacy
"""

from core import telnet_compat as telnetlib
import time
import logging
import re
from typing import Optional

from netmiko import ConnectHandler
from netmiko.base_connection import BaseConnection

from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)


def parsing_cisco_fan_status(output: str) -> str:
    """Cisco 'show env all' 출력에서 팬 상태 파싱"""
    fan_status = re.findall(r"FAN(?: \d+)? (?:is )?(.+)", output)
    if not fan_status:
        return ""
    normalized = [status.upper() for status in fan_status]
    if any("FAULTY" in status for status in normalized):
        return "FAULTY"
    if all("OK" in status for status in normalized if "NOT PRESENT" not in status):
        return "OK"
    return "UNKNOWN"


def parsing_cisco_temperature(output: str) -> str:
    """Cisco 'show env all' 출력에서 온도 파싱"""
    match = re.search(r"TEMPERATURE is (.+)", output, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parsing_cisco_hostname(output: str) -> str:
    """Cisco running-config 출력에서 호스트네임 파싱"""
    match = re.search(r'hostname\s+"?(\S+)"?', output)
    return match.group(1) if match else ""


# Cisco 장비 점검 명령어 정의
CISCO_INSPECTION_COMMANDS = {
    'cisco': {
        'ios': [
            'show running-config',
            'show env all',
            'show version | include uptime',
            'show process cpu | include CPU utilization',
            'show process memory | include Processor Pool'
        ],
        'ios-xe': [
            'show version',
            'show running-config'
        ],
        'legacy': [
            'show version',
            'show running-config'
        ]
    }
}

# Cisco 장비 설정 백업 명령어 정의
CISCO_BACKUP_COMMANDS = {
    'cisco': {
        'ios': 'show running-config',
        'ios-xe': 'show running-config',
        'legacy': 'show running-config'
    }
}

# Cisco 장비 출력 파싱 규칙
CISCO_PARSING_RULES = {
    'cisco': {
        'ios': {
            'show running-config': {
                'custom_parser': 'parsing_cisco_hostname',
                'output_column': 'Hostname'
            },
            'show env all': {
                'patterns': [
                    {
                        'custom_parser': 'parsing_cisco_fan_status',
                        'output_column': 'Fan Status'
                    },
                    {
                        'custom_parser': 'parsing_cisco_temperature',
                        'output_column': 'System Temperature'
                    }
                ]
            },
            'show version | include uptime': {
                'pattern': r'uptime is (.+)',
                'output_column': 'Uptime',
                'first_match_only': True
            },
            'show process cpu | include CPU utilization': {
                'pattern': r'CPU utilization for five seconds:\s+(\d+)%/\d+%;',
                'output_column': 'CPU Usage',
                'first_match_only': True
            },
            'show process memory | include Processor Pool': {
                'pattern': r'Used:\s*(\d+)',
                'output_column': 'Memory Usage',
                'first_match_only': True
            }
        },
        'legacy': {
            'show version': {
                'patterns': [
                    {
                        'pattern': r'(?:Cisco IOS Software|IOS \(tm\)).*?Version\s+([^\s,]+)',
                        'output_column': 'Version',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'([^\s]+) uptime is (.+)',
                        'output_columns': ['Hostname', 'Uptime'],
                        'first_match_only': True
                    },
                    {
                        'pattern': r'(?:cisco|Cisco)\s+([^\s(]+).*\s+with\s+',
                        'output_column': 'Model',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Processor board ID\s+(\S+)',
                        'output_column': 'Serial Number',
                        'first_match_only': True
                    }
                ]
            }
        }
    }
}


@register_handler('cisco', 'ios', 'ssh')
class CiscoIosSSHHandler(CustomDeviceHandler):
    """Cisco IOS SSH 장비 핸들러 (Netmiko 기반)"""

    def __init__(self, device, timeout: int = 30, session_log_file: Optional[str] = None):
        super().__init__(device, timeout, session_log_file)
        self.conn: Optional[BaseConnection] = None

    def _build_params(self) -> dict:
        enable_password = self.device.get("enable_password") or self.device.get("password")
        params = {
            "device_type": "cisco_ios",
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
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("CiscoIosSSHHandler는 SSH 연결만 지원합니다")
        try:
            self.conn = ConnectHandler(**self._build_params())
            self.logger.debug("Cisco IOS 접속 성공: %s", self.device.get("ip"))
            return True
        except Exception as exc:
            self.logger.error("Cisco IOS SSH 접속 실패: %s", exc)
            self.conn = None
            raise

    def enable(self) -> None:
        if not self.conn:
            raise ConnectionError("Netmiko 연결이 초기화되지 않았습니다.")
        self.conn.enable()
        output = self.conn.send_command_timing("terminal length 0")
        self.log_output("terminal length 0 명령어 후", output)

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


@register_handler('cisco', 'legacy', 'telnet')
class CiscoLegacyTelnetHandler(CustomDeviceHandler):
    """Legacy Cisco 장비 Telnet 핸들러 (username 없이 password만 사용)"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.tn = None
    
    def connect(self):
        """텔넷으로 장비에 연결"""
        if self.device['connection_type'].lower() != 'telnet':
            raise ValueError("CiscoLegacyTelnetHandler는 텔넷 연결만 지원합니다")
        
        self.logger.debug("Legacy Cisco 장비 Telnet 접속 시작: %s", self.device['ip'])
        
        self.tn = telnetlib.Telnet(self.device['ip'], port=self.device['port'], timeout=self.timeout)
        
        try:
            # 초기 접속 시 Password: 프롬프트가 나타날 때까지 대기
            index, match, text = self.tn.expect([b"Password:", b"Username:"], timeout=20)
            
            # 초기 출력 로깅
            output = text.decode('utf-8', errors='ignore')
            self.log_output("초기 프롬프트", output)
            
            # Password 프롬프트가 먼저 나온 경우 (username이 필요 없는 경우)
            if index == 0:
                self.tn.write(self.device['password'].encode('utf-8') + b"\n")
                time.sleep(2)
            # Username 프롬프트가 먼저 나온 경우 (일반적인 경우)
            else:
                # username이 제공된 경우에만 사용
                if 'username' in self.device and self.device['username']:
                    self.tn.write(self.device['username'].encode('utf-8') + b"\n")
                    time.sleep(1)
                else:
                    # username이 제공되지 않은 경우 엔터 키 입력
                    self.tn.write(b"\n")
                    time.sleep(1)
                
                # Password 입력
                self.tn.read_until(b"Password:", timeout=10)
                self.tn.write(self.device['password'].encode('utf-8') + b"\n")
                time.sleep(2)
            
            # 로그인 후 출력 확인
            output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
            self.log_output("로그인 후 출력", output)
            
            # 로그인 성공 확인 (프롬프트에 > 또는 # 포함 확인)
            if ">" in output or "#" in output:
                self.logger.debug("로그인 성공: %s", self.device['ip'])
                return True
            else:
                # 추가 시간 대기 후 다시 확인
                time.sleep(3)
                output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
                
                if ">" in output or "#" in output:
                    self.logger.debug("로그인 성공 (추가 대기 후): %s", self.device['ip'])
                    return True
                else:
                    self.logger.warning("로그인 상태 불확실: %s", self.device['ip'])
                    # 계속 진행
                    return True
            
        except Exception as e:
            self.logger.error("Legacy Cisco Telnet 접속 실패: %s", e)
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as log:
                    log.write(f"\n접속 실패: {str(e)}\n")
            
            # 세션 닫기
            if self.tn:
                self.tn.close()
                self.tn = None
            
            raise
    
    def enable(self):
        """특권 모드 진입"""
        self.logger.debug("Legacy Cisco 장비 enable 모드 진입 시도: %s", self.device['ip'])
        
        try:
            # 현재 프롬프트 확인
            self.tn.write(b"\n")
            time.sleep(1)
            output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
            
            # enable 모드(#)인지 확인
            if "#" in output:
                self.logger.debug("이미 enable 모드 상태: %s", self.device['ip'])
                self.log_output("현재 프롬프트 (이미 enable 모드)", output)
                return True
            
            # enable 명령 실행
            self.tn.write(b"enable\n")
            time.sleep(1)
            
            # Password 프롬프트 대기
            index, match, text = self.tn.expect([b"Password:", b"#"], timeout=5)
            
            # Password 프롬프트가 나온 경우
            if index == 0:
                # enable_password가 설정된 경우 해당 값 사용, 아니면 기본 password 사용
                password = self.device.get('enable_password', self.device['password'])
                self.tn.write(password.encode('utf-8') + b"\n")
                time.sleep(2)
            
            # enable 모드 진입 확인
            output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
            self.log_output("enable 명령 실행 후 출력", output)
            
            if "#" in output:
                self.logger.debug("enable 모드 진입 성공: %s", self.device['ip'])
                
                # terminal length 0 설정
                self.tn.write(b"terminal length 0\n")
                time.sleep(1)
                output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
                self.log_output("terminal length 0 명령 실행 결과", output)
                
                return True
            else:
                self.logger.warning("enable 모드 진입 실패: %s", self.device['ip'])
                return False
                
        except Exception as e:
            self.logger.error("enable 모드 진입 중 오류: %s", e)
            return False
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 5
        
        self.log_output(f"명령어 실행: {command}", "")
        
        try:
            # 명령어 전송
            self.tn.write(command.encode('utf-8') + b"\n")
            time.sleep(timeout)
            
            # 결과 읽기
            output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
            
            # 명령어와 프롬프트 제거 처리
            lines = output.splitlines()
            
            # '--More--' 처리
            full_output = output
            
            while "--More--" in full_output:
                self.tn.write(b" ")  # Space를 보내서 더 보기
                time.sleep(1)
                chunk = self.tn.read_very_eager().decode('utf-8', errors='ignore')
                full_output += chunk
                
                # 무한루프 방지 (일정 크기 이상이면 종료)
                if len(full_output) > 1000000:  # 약 1MB
                    break
            
            # 출력 정리
            lines = full_output.splitlines()
            
            # 첫 줄에 명령어 자체가 있으면 제거
            if lines and command in lines[0]:
                lines = lines[1:]
            
            # 마지막 줄이 프롬프트인 경우 제거
            if lines and (lines[-1].strip().endswith(">") or lines[-1].strip().endswith("#")):
                lines = lines[:-1]
            
            # '--More--' 제거
            cleaned_lines = []
            for line in lines:
                if "--More--" in line:
                    more_text = "--More--"
                    cleaned_line = line.split(more_text)[0].strip()
                    if cleaned_line:
                        cleaned_lines.append(cleaned_line)
                else:
                    cleaned_lines.append(line)
            
            result = "\n".join(cleaned_lines)
            self.log_output("명령어 결과", result)
            
            return result
            
        except Exception as e:
            self.logger.error("명령어 실행 실패 (%s): %s", command, e)
            return f"Error executing command: {str(e)}"
    
    def disconnect(self):
        """연결 종료"""
        if self.tn:
            try:
                self.tn.write(b"exit\n")
                time.sleep(1)
            except Exception:
                pass
            
            try:
                self.tn.close()
            except Exception:
                pass
            
            self.tn = None
            
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as log:
                    log.write(f"\n{'='*50}\n")
                    log.write("세션 종료\n")
                    log.write(f"{'='*50}\n") 
