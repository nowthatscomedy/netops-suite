"""
Network Device Inspection Tool - Ruckus Module

Ruckus ICX 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: icx
"""

import time
import logging
import paramiko
import re
from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)

# --- Ruckus 커스텀 파싱 함수 ---

def parsing_ruckus_power(output: str) -> str:
    """Ruckus 'show chassis' 명령어 출력에서 파워 상태 파싱"""
    power_supply_statuses = re.findall(r'Power supply \d+ .*? status (\w+)', output)
    if not power_supply_statuses:
        return "Not Found"
    if all(status == 'ok' for status in power_supply_statuses):
        return "OK"
    bad_power = [f"PSU{i+1}({s})" for i, s in enumerate(power_supply_statuses) if s != 'ok']
    return f"Check: {', '.join(bad_power)}"

def parsing_ruckus_fan(output: str) -> str:
    """Ruckus 'show chassis' 명령어 출력에서 팬 상태 파싱"""
    fan_statuses = re.findall(r'Fan \d+ (\w+), speed', output)
    if not fan_statuses:
        return "Not Found"
    if all(status == 'ok' for status in fan_statuses):
        return "OK"
    bad_fans = [f"Fan{i+1}({s})" for i, s in enumerate(fan_statuses) if s != 'ok']
    return f"Check: {', '.join(bad_fans)}"

def parsing_ruckus_temp(output: str) -> str:
    """Ruckus 'show chassis' 명령어 출력에서 온도 파싱"""
    slot_temp_lines = re.findall(r'Slot \d+ Current Temperature: (.*)', output)
    all_temps = []
    for line in slot_temp_lines:
        temps_in_line = re.findall(r'(\d+\.\d+) deg-C', line)
        all_temps.extend([float(t) for t in temps_in_line])
    if all_temps:
        max_temp = max(all_temps)
        return f"{max_temp} C"
    return ""

def parsing_ruckus_cpu(output: str) -> str:
    """Ruckus 'show cpu-utilization' 명령어 출력에서 CPU 사용량 파싱"""
    match = re.search(r'60\s+sec avg:\s+(\d+)\s*percent busy', output)
    if match:
        return f"{match.group(1)}%"
    return ""

def parsing_ruckus_memory(output: str) -> dict:
    """Ruckus 'show memory' 명령어 출력에서 메모리 정보 파싱"""
    results = {}
    match = re.search(r'Dynamic memory: (\d+) bytes total, (\d+) bytes free, (\d+)% used', output)
    if match:
        total = int(match.group(1))
        free = int(match.group(2))
        used_percent = int(match.group(3))
        used = total - free
        
        results['Memory Total'] = f"{total} bytes"
        results['Memory Used'] = f"{used} bytes"
        results['Memory Free'] = f"{free} bytes"
        results['Memory Usage'] = f"{used_percent}%"
    return results

# Ruckus 장비 점검 명령어 정의
RUCKUS_INSPECTION_COMMANDS = {
    'ruckus': {
        'icx': [
            'show running-config',
            'show chassis',
            'show version',
            'show cpu-utilization',
            'show memory',
        ]
    }
}

# Ruckus 장비 설정 백업 명령어 정의
RUCKUS_BACKUP_COMMANDS = {
    'ruckus': {
        'icx': 'show running-config'
    }
}

# Ruckus 장비 출력 파싱 규칙
RUCKUS_PARSING_RULES = {
    'ruckus': {
        'icx': {
            'show running-config': {
                'pattern': r'hostname\s+(\S+)',
                'output_column': 'Hostname',
                'first_match_only': True
            },
            'show chassis': {
                'patterns': [
                    {'custom_parser': 'parsing_ruckus_power', 'output_column': 'Power Status'},
                    {'custom_parser': 'parsing_ruckus_fan', 'output_column': 'Fan Status'},
                    {'custom_parser': 'parsing_ruckus_temp', 'output_column': 'System Temperature'},
                ]
            },
            'show version': {
                'patterns': [
                    {'pattern': r'SW: Version\s+(\S+)', 'output_column': 'Version', 'first_match_only': True},
                    {'pattern': r'HW: Stackable\s+(.*)', 'output_column': 'Model', 'first_match_only': True},
                    {'pattern': r'system uptime is\s+(.+)', 'output_column': 'Uptime', 'first_match_only': True},
                    {'pattern': r'Serial\s+#:(\S+)', 'output_column': 'Serial Number', 'first_match_only': True}
                ]
            },
            'show cpu-utilization': {
                'custom_parser': 'parsing_ruckus_cpu',
                'output_column': 'CPU Usage'
            },
            'show memory': {
                'custom_parser': 'parsing_ruckus_memory',
            }
        }
    }
}

@register_handler('ruckus', 'icx', 'ssh')
class RuckusIcxSSHHandler(CustomDeviceHandler):
    """Ruckus ICX SSH 장비 핸들러"""

    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt = None

    def connect(self):
        """SSH로 장비에 연결"""
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("RuckusIcxSSHHandler는 SSH 연결만 지원합니다")

        self.logger.debug("Ruckus ICX 장비 SSH 접속 시작: %s", self.device['ip'])

        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.ssh.connect(
                hostname=self.device['ip'],
                username=self.device['username'],
                password=self.device['password'],
                port=int(self.device['port']),
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False
            )

            self.channel = self.ssh.invoke_shell(width=200, height=1000)
            self.channel.settimeout(self.timeout)

            time.sleep(2)
            output = self._read_channel()
            self.log_output("초기 출력", output)

            if ">" in output or "#" in output:
                # Find prompt from the last line
                last_line = output.strip().splitlines()[-1]
                self.prompt = last_line.strip()
                self.logger.debug("Ruckus ICX 접속 성공 및 프롬프트 확인: %s", self.prompt)
                return True
            else:
                self.logger.error("Ruckus ICX 접속 실패: 프롬프트를 찾을 수 없습니다.")
                raise ConnectionError("Ruckus ICX 접속 실패: 프롬프트를 찾을 수 없습니다.")

        except Exception as e:
            self.logger.error("Ruckus ICX SSH 접속 실패: %s", e)
            if self.ssh:
                self.ssh.close()
            raise

    def _read_channel(self):
        """채널에서 출력 읽기"""
        output = ""
        time.sleep(0.5)
        if self.channel.recv_ready():
            while self.channel.recv_ready():
                output += self.channel.recv(65535).decode('utf-8', 'ignore')
                time.sleep(0.1)
        return output

    def enable(self):
        """특권 모드 진입"""
        self.logger.debug("Ruckus ICX enable 모드 진입 시도: %s", self.device['ip'])

        self.channel.send('\n')
        time.sleep(0.5)
        output = self._read_channel()
        last_line = output.strip().splitlines()[-1] if output.strip() else ''
        self.prompt = last_line.strip()

        if "#" in self.prompt:
            self.logger.debug("이미 특권 모드입니다.")
        elif ">" in self.prompt:
            self.channel.send('enable\n')
            time.sleep(1)
            output = self._read_channel()
            self.log_output("enable 명령어 후", output)

            if "Password:" in output:
                enable_password = self.device.get('enable_password', self.device['password'])
                self.channel.send(enable_password + '\n')
                time.sleep(1)
                output = self._read_channel()
                self.log_output("enable 비밀번호 입력 후", output)

            last_line = output.strip().splitlines()[-1] if output.strip() else ''
            if "#" in last_line:
                self.prompt = last_line.strip()
                self.logger.debug("특권 모드 진입 성공. 새 프롬프트: %s", self.prompt)
            else:
                self.logger.warning("특권 모드 진입에 실패했을 수 있습니다.")

        self.logger.debug("페이지 넘김 비활성화 시도")
        self.channel.send("skip-page-display\n")
        time.sleep(1)
        output = self._read_channel()
        self.log_output("skip-page-display 명령어 후", output)

    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 10

        if not self.channel:
            raise ConnectionError("SSH 채널이 연결되지 않았습니다.")

        self.log_output(f"명령어 실행: {command}", "")

        self._read_channel() # Clear buffer

        self.channel.send(command + "\n")

        full_output = ""
        time.sleep(2)

        max_pages = 50
        for _ in range(max_pages):
            output_chunk = self._read_channel()
            full_output += output_chunk
            
            if "--More--" in output_chunk:
                self.channel.send(" ")
                time.sleep(1)
            else:
                break
        
        output = full_output
        lines = output.splitlines()

        if not lines:
            return ""

        if command.strip() in lines[0]:
            lines = lines[1:]

        if lines and self.prompt and self.prompt in lines[-1]:
            lines = lines[:-1]

        cleaned_output = "\n".join(lines).strip()
        self.log_output("정리된 명령어 결과", cleaned_output)

        return cleaned_output

    def disconnect(self):
        """SSH 연결 종료"""
        self.logger.debug("Ruckus ICX SSH 연결 종료: %s", self.device['ip'])
        if self.channel:
            self.channel.close()
        if self.ssh:
            self.ssh.close()

        self.channel = None
        self.ssh = None

        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write(f"\n{'='*50}\n")
                log.write(f"세션 종료\n")
                log.write(f"{'='*50}\n\n") 