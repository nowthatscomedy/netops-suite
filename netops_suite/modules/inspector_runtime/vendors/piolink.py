"""
Network Device Inspection Tool - Piolink Module

Piolink 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: tifront
"""

import time
import logging
import paramiko
import re
import datetime
from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)

# --- Piolink 커스텀 파싱 함수들 ---
def parsing_piolink_login_count(output: str) -> str:
    """piolink 'show log user' 전체 출력에서 최신 연도 구간의 해당 월 'Log In' 횟수 파싱

    규칙:
    - 로그는 최신 연도가 앞쪽에 오며, 월이 역순으로 진행되다 12->1로 넘어가는 지점에서 연도가 바뀌었다고 가정
    - 해당 지점 이전(상단)만 최신 연도 로그로 간주
    - 대상 월은 현재 달(현 시스템 시간 기준)
    - 라인에서 월 표기는 영문 약어(Jan..Dec) 또는 한국어 'n월' 모두 지원
    """
    lines = output.splitlines()

    # 월 매핑 (영문 약어 → 숫자)
    month_map = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }

    # 현재 월 (대상 월)
    try:
        target_month = int(datetime.datetime.now().strftime('%m'))
    except Exception:
        target_month = None

    # 각 라인에서 월 번호 추출 함수
    def extract_month_number(line: str):
        # 영문 약어 우선
        m = re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b', line, re.IGNORECASE)
        if m:
            return month_map.get(m.group(1).lower())
        # 한국어 n월
        m2 = re.search(r'(1[0-2]|[1-9])\s*월', line)
        if m2:
            try:
                return int(m2.group(1))
            except ValueError:
                return None
        return None

    # 최신 연도 경계 탐지: 월 번호가 증가하는 첫 지점
    last_month = None
    boundary_index = len(lines)  # 기본값: 경계 없음 → 전체가 최신 연도
    for idx, line in enumerate(lines):
        month_num = extract_month_number(line)
        if month_num is None:
            continue
        if last_month is not None and month_num > last_month:
            boundary_index = idx
            break
        last_month = month_num

    # 최신 연도 구간(0 ~ boundary_index-1) 중 대상 월의 'Log In' 카운트
    if target_month is None:
        # 안전장치: 대상 월을 알 수 없으면 최신 연도 구간 전체에서 'Log In' 카운트
        latest_lines = lines[:boundary_index]
        return str(sum(1 for ln in latest_lines if 'Log In' in ln))

    count = 0
    for ln in lines[:boundary_index]:
        if 'Log In' not in ln:
            continue
        ln_month = extract_month_number(ln)
        if ln_month == target_month:
            count += 1
    return str(count)

def parsing_piolink_port_up_count(output: str) -> str:
    """piolink 'show portstatus' 명령어 출력에서 UP 상태인 포트 수 파싱"""
    count = 0
    for line in output.splitlines():
        if re.match(r"^\s*ge\d+\s*\|", line.strip()):
            parts = [p.strip() for p in line.split('|')]
            if len(parts) > 2 and parts[2] == "UP":
                count += 1
    return str(count)

def parsing_piolink_poe_enable_count(output: str) -> str:
    """piolink 'show poe-info' 명령어 출력에서 Enable 상태인 포트 수 파싱"""
    count = 0
    for line in output.splitlines():
        # 데이터 라인인지 확인 (e.g., "  ge1  | ...")
        if line.strip().startswith("ge"):
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 4 and parts[3] == "Enable":
                count += 1
    return str(count)

# Piolink 장비 점검 명령어 정의
PIOLINK_INSPECTION_COMMANDS = {
    'piolink': {
        'tifront': [
            'show version',
            'show system',
            'show resource',
            'show_log_user_this_month', # 동적 명령어 플레이스홀더
            'show portstatus',
            'show poe-info',
            'show uptime',
            'show running-config' # For hostname
        ]
    }
}

# Piolink 장비 설정 백업 명령어 정의
PIOLINK_BACKUP_COMMANDS = {
    'piolink': {
        'tifront': 'show running-config'
    }
}

# Piolink 장비 출력 파싱 규칙
PIOLINK_PARSING_RULES = {
    'piolink': {
        'tifront': {
            'show version': {
                'pattern': r'(.+)',
                'output_column': 'Version',
                'first_match_only': True
            },
            'show system': {
                'patterns': [
                    {
                        'pattern': r'Product Name\s+:\s+(.+)',
                        'output_column': 'Model',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Serial number\s+:\s+(.+)',
                        'output_column': 'Serial Number',
                        'first_match_only': True
                    }
                ]
            },
            'show resource': {
                'patterns': [
                    {
                        'pattern': r'CPU Usage\s+:\s+(.*)',
                        'output_column': 'CPU Usage'
                    },
                    {
                        'pattern': r'Total Memory:\s+(.*)',
                        'output_column': 'Memory Total'
                    },
                    {
                        'pattern': r'Used Memory\s+:\s+(.*)',
                        'output_column': 'Memory Used'
                    },
                    {
                        'pattern': r'Free Memory\s+:\s+(.*)',
                        'output_column': 'Memory Free'
                    },
                    {
                        'pattern': r'Memory Usage:\s+(.*)',
                        'output_column': 'Memory Usage'
                    }
                ]
            },
            'show_log_user_this_month': {
                'custom_parser': 'parsing_piolink_login_count',
                'output_column': '월별 로그인 수'
            },
            'show portstatus': {
                'custom_parser': 'parsing_piolink_port_up_count',
                'output_column': 'UP Port Count'
            },
            'show poe-info': {
                'custom_parser': 'parsing_piolink_poe_enable_count',
                'output_column': 'PoE Port Count'
            },
            'show uptime': {
                'pattern': r'^(\d+\s+days)\b',
                'output_column': 'Uptime',
                'first_match_only': True
            },
            'show running-config': {
                'pattern': r'^hostname\s+(\S+)',
                'output_column': 'Hostname',
                'first_match_only': True
            }
        }
    }
}


@register_handler('piolink', 'tifront', 'ssh')
class PiolinkTifrontSSHHandler(CustomDeviceHandler):
    """Piolink Tifront SSH 장비 핸들러"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt = None
    
    def connect(self):
        """SSH로 장비에 연결"""
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("PiolinkTifrontSSHHandler는 SSH 연결만 지원합니다")
            
        self.logger.debug("Piolink Tifront 장비 SSH 접속 시작: %s", self.device['ip'])
        
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
                self.logger.debug("Piolink Tifront 접속 성공 및 프롬프트 확인: %s", self.prompt)
                return True
            else:
                self.logger.error("Piolink Tifront 접속 실패: 프롬프트를 찾을 수 없습니다.")
                raise ConnectionError("Piolink Tifront 접속 실패: 프롬프트를 찾을 수 없습니다.")

        except Exception as e:
            self.logger.error("Piolink Tifront SSH 접속 실패: %s", e)
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
        self.logger.debug("Piolink Tifront enable 모드 진입 시도: %s", self.device['ip'])
        
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
        actual_command = command
        if command == 'show_log_user_this_month':
            # 연도 경계 감지를 위해 전체 로그를 본다 (include 제거)
            actual_command = 'show log user'
            self.logger.debug("Dynamic command generated: %s", actual_command)

        if timeout is None:
            timeout = 20
        
        if not self.channel:
            raise ConnectionError("SSH 채널이 연결되지 않았습니다.")

        self.log_output(f"명령어 실행: {actual_command}", "")
        
        self._read_channel() # Clear buffer
        
        self.channel.send(actual_command + "\n")
        
        full_output = ""
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            time.sleep(0.5) # Wait for network latency and command execution
            
            if self.channel.recv_ready():
                chunk = self.channel.recv(65535).decode('utf-8', 'ignore')
                full_output += chunk
                
                # Check if command output has ended by finding the prompt
                if self.prompt and self.prompt in full_output:
                    break
        else:
            self.logger.warning("명령어 실행 시간 초과 또는 프롬프트를 찾을 수 없음: %s", actual_command)

        output = full_output
        lines = output.splitlines()
        
        if not lines:
            return ""
            
        if actual_command.strip() in lines[0]:
            lines = lines[1:]
        
        if lines and self.prompt and self.prompt in lines[-1]:
            lines = lines[:-1]

        cleaned_output = "\n".join(lines).strip()
        cleaned_output = cleaned_output.replace('\r', '')
        self.log_output("정리된 명령어 결과", cleaned_output)
        
        return cleaned_output

    def disconnect(self):
        """SSH 연결 종료"""
        self.logger.debug("Piolink Tifront SSH 연결 종료: %s", self.device['ip'])
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