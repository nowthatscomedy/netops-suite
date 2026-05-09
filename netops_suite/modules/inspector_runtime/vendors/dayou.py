"""
Network Device Inspection Tool - DAYOU Module

DAYOU 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: dsw
"""

import time
import logging
import paramiko
import re
from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)

# --- DAYOU 커스텀 파싱 함수 ---
def parsing_dayou_memory_usage(output: str) -> str:
    """DAYOU 'show memory static' 명령어 출력에서 메모리 사용률 파싱"""
    match = re.search(r"total\s+(\d+)\s+bytes,\s+current used\s+(\d+)\s+bytes", output)
    if match:
        total = int(match.group(1))
        used = int(match.group(2))
        if total > 0:
            usage = int((used / total) * 100)
            return f"{usage}%"
    return ""

def parsing_dayou_poe_count(output: str) -> str:
    """DAYOU 'show poe power' 명령어 출력에서 PoE 사용 포트 수 파싱"""
    count = 0
    for line in output.splitlines():
        if re.match(r'^\s*g\d+/\d+', line.strip()):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    current_power = int(parts[1])
                    if current_power > 0:
                        count += 1
                except (ValueError, IndexError):
                    continue
    return str(count)

def parsing_dayou_up_port_count(output: str) -> str:
    """DAYOU 'show interface brief' 명령어 출력에서 UP 상태인 포트 수 파싱"""
    count = 0
    for line in output.splitlines():
        line = line.strip()
        if not line or line.lower().startswith('port'):
            continue
        parts = line.split()
        if len(parts) >= 2:
            port, status = parts[0], parts[1].lower()
            if not port.startswith('v') and status == 'up':
                count += 1
    return str(count)

# DAYOU 장비 점검 명령어 정의
DAYOU_INSPECTION_COMMANDS = {
    'dayou': {
        'dsw': [
            'show version',
            'show cpu',
            'show memory static | include total',
            'show poe power',
            'show interface brief',
        ]
    }
}

# DAYOU 장비 설정 백업 명령어 정의
DAYOU_BACKUP_COMMANDS = {
    'dayou': {
        'dsw': 'show running-config'
    }
}

# DAYOU 장비 출력 파싱 규칙
DAYOU_PARSING_RULES = {
    'dayou': {
        'dsw': {
            'show version': {
                'patterns': [
                    {
                        'pattern': r'^(DSW\S+)\s+Series Software',
                        'output_column': 'Model',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'^(\S+)\s+uptime is',
                        'output_column': 'Hostname',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Version\s+([\d\.]+[A-Z]?)',
                        'output_column': 'Version',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'uptime is\s+([\d:]+)',
                        'output_column': 'Uptime',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Serial num:([^,]+)',
                        'output_column': 'Serial Number',
                        'first_match_only': True
                    }
                ]
            },
            'show cpu': {
                'pattern': r'one minute:\s*(\d+%)',
                'output_column': 'CPU Usage',
                'first_match_only': True
            },
            'show memory static | include total': {
                'custom_parser': 'parsing_dayou_memory_usage',
                'output_column': 'Memory Usage'
            },
            'show poe power': {
                'custom_parser': 'parsing_dayou_poe_count',
                'output_column': 'PoE Port Count'
            },
            'show interface brief': {
                'custom_parser': 'parsing_dayou_up_port_count',
                'output_column': 'UP Port Count'
            }
        }
    }
}


@register_handler('dayou', 'dsw', 'ssh')
class DayouDswSshHandler(CustomDeviceHandler):
    """DAYOU DSW SSH 장비 핸들러"""

    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt = None

    def connect(self):
        """SSH로 장비에 연결"""
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("DayouDswSshHandler는 SSH 연결만 지원합니다")

        self.logger.debug("DAYOU DSW 장비 SSH 접속 시작: %s", self.device['ip'])

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
                self.logger.debug("DAYOU DSW 접속 성공 및 프롬프트 확인: %s", self.prompt)
                return True
            else:
                self.logger.error("DAYOU DSW 접속 실패: 프롬프트를 찾을 수 없습니다.")
                raise ConnectionError("DAYOU DSW 접속 실패: 프롬프트를 찾을 수 없습니다.")

        except Exception as e:
            self.logger.error("DAYOU DSW SSH 접속 실패: %s", e)
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
        self.logger.debug("DAYOU DSW enable 모드 진입 시도: %s", self.device['ip'])

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

        self.logger.debug("터미널 길이 설정 시도")
        self.channel.send("terminal length 0\n")
        time.sleep(1)
        output = self._read_channel()
        self.log_output("terminal length 0 명령어 후", output)

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
                # Check for prompt to ensure command is finished
                if self.prompt and self.prompt in full_output:
                    break
        
        output = full_output.replace('\r', '')
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
        self.logger.debug("DAYOU DSW SSH 연결 종료: %s", self.device['ip'])
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
