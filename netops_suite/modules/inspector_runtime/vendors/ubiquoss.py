"""
Network Device Inspection Tool - Ubiquoss Module

Ubiquoss 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: e4020
"""

from core import telnet_compat as telnetlib
import time
import logging
from vendors.base import CustomDeviceHandler, register_handler
import paramiko
import re

logger = logging.getLogger(__name__)

# Ubiquoss 장비 점검 명령어 정의
UBIQUOSS_INSPECTION_COMMANDS = {
    'ubiquoss': {
        'e4020': [
            'show running-config', # Hostname
            'show system',         # Model, Serial Number, S/W Version
            'show cpu usage',      # CPU Usage (명령어 확인)
            'show memory usage', # Memory Usage (명령어 변경)
            'show environment temperature', # Temperature, Fan (온도와 팬 명령어가 동일하여 분리 필요 - 팬은 cooling으로 변경)
            'show environment cooling',     # Fan Status
            'show environment power',       # Power Status
            'show uptime'          # Uptime
        ]
    }
}

# Ubiquoss 장비 설정 백업 명령어 정의
UBIQUOSS_BACKUP_COMMANDS = {
    'ubiquoss': {
        'e4020': 'show running-config'
    }
}

# --- 새로운 커스텀 파싱 함수들 ---
def parsing_ubiquoss_cpu_usage(output: str) -> str:
    # 예시 출력:
    #   5 min :   3.67%
    match = re.search(r"5 min\s*:\s*(\d+\.\d+)%", output)
    if match:
        cpu_usage = match.group(1)
        return f"{cpu_usage}%" # 숫자만 반환 후 % 추가
    return "Error: CPU 5min average not found"

def parsing_ubiquoss_fan_status(output: str) -> str:
    warnings = []
    # 예시: fan-module 1
    #         fan-tray 1 fan-status: OK
    #         fan-tray 2 fan-status: FAIL
    current_module = None
    for line in output.splitlines():
        module_match = re.search(r"fan-module\\s+(\\d+)", line)
        if module_match:
            current_module = module_match.group(1)
            continue
        
        status_match = re.search(r"fan-tray\\s+(\\d+)\\s+fan-status:\\s*(?!OK)(\\S+)", line)
        if status_match and current_module:
            tray_num = status_match.group(1)
            # status_val = status_match.group(2) # 상태 값 (e.g., FAIL)
            warnings.append(f"{current_module}-{tray_num}")
            
    if not warnings:
        return "OK"
    else:
        return "WARN: " + ", ".join(warnings)

def parsing_ubiquoss_power_status(output: str) -> str:
    warnings = []
    # 예시: power-supply 1
    #         power-supply 1 power-input: AC
    #         power-supply 1 power-output-status: FAIL
    current_supply = None
    for line in output.splitlines():
        supply_match = re.search(r"power-supply\\s+(\\d+)", line)
        if supply_match:
            current_supply = supply_match.group(1)
            # 다음 라인에서 상태를 확인하므로, 여기서는 current_supply만 업데이트
        
        # "power-supply X power-output-status: Y" 패턴과 일치하는지 확인
        # current_supply가 설정된 상태에서만 이 라인을 유효하게 처리
        if current_supply:
            status_match = re.search(r"power-supply\\s+" + re.escape(current_supply) + r"\\s+power-output-status:\\s*(?!OK)(\\S+)", line)
            if status_match:
                # status_val = status_match.group(1) # 상태 값 (e.g., FAIL)
                warnings.append(f"{current_supply}-1") # Ubiquoss는 파워 번호가 항상 1로 고정된 것처럼 보임. 예시가 하나뿐이라 일반화.
                                                      # 만약 파워 번호가 다를 수 있다면, 파싱 로직 수정 필요. 여기서는 일단 1로 가정.
    
    if not warnings:
        return "OK"
    else:
        return "WARN: " + ", ".join(warnings)

# Ubiquoss 장비 출력 파싱 규칙
UBIQUOSS_PARSING_RULES = {
    'ubiquoss': {
        'e4020': {
            'show running-config': {
                'pattern': r"^\s*hostname\s+(\S+)",
                'output_column': 'Hostname',
                'first_match_only': True
            },
            'show system': {
                'patterns': [
                    {
                        'pattern': r"^\s*Model Name\s*:\s*(\S+)",
                        'output_column': 'Model',
                        'first_match_only': True
                    },
                    {
                        'pattern': r"^\s*Serial Number\s*:\s*(\S+)",
                        'output_column': 'Serial Number',
                        'first_match_only': True
                    },
                    {
                        'pattern': r"^\s*S/W Version\s*:\s*(\S+)",
                        'output_column': 'Version',
                        'first_match_only': True
                    }
                ]
            },
            'show cpu usage': {
                'custom_parser': 'parsing_ubiquoss_cpu_usage',
                'output_column': 'CPU Usage'
            },
            'show memory usage': { # 명령어 키 및 파싱 규칙 변경
                'patterns': [
                    {
                        # 예: 506916K total,   245216K used,   261700K free,  51.63% available
                        'pattern': r"(\d+K)\s+total,\s*(\d+K)\s+used,\s*(\d+K)\s+free,\s*(\d+\.\d+)%\s+available",
                        'output_columns': ['Memory Total', 'Memory Used', 'Memory Free', 'Memory Available %'],
                        'first_match_only': True,
                        'process': { # Memory Usage % 계산 추가
                            'type': 'calculate_usage_from_available',
                            'input_column': 'Memory Available %', # 이 값을 사용
                            'output_column': 'Memory Usage'
                        }
                    }
                ]
            },
            'show environment temperature': { # 온도는 이 명령어로
                'pattern': r"^\s*current\s*:\s*(.+)", # "current" 라인에서 콜론 이후 모든 문자열 추출
                'output_column': 'System Temperature',
                'first_match_only': True
            },
            'show environment cooling': { # 팬은 이 명령어로
                'custom_parser': 'parsing_ubiquoss_fan_status',
                'output_column': 'Fan Status'
            },
            'show environment power': {
                'custom_parser': 'parsing_ubiquoss_power_status',
                'output_column': 'Power Status'
            },
            'show uptime': {
                # 예: 0 days, 1 hours, 8 mins, 58 secs since boot
                # 또는 3 hours, 10 mins, 5 secs since boot
                # 또는 10 mins, 5 secs since boot
                # 또는 5 secs since boot
                'pattern': r"((?:\d+\s+days,\s+)?(?:\d+\s+hours,\s+)?(?:\d+\s+mins,\s+)?\d+\s+secs)\s+since boot",
                'output_column': 'Uptime',
                'first_match_only': True
            }
        }
    }
}

@register_handler('ubiquoss', 'e4020', 'ssh')
class UbiquossE4020SSHHandler(CustomDeviceHandler):
    """Ubiquoss E4020 장비 SSH 핸들러"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt_char = None # 현재 프롬프트 문자 ('>' 또는 '#')
    
    def connect(self):
        """SSH로 장비에 연결"""
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("UbiquossE4020SSHHandler는 SSH 연결만 지원합니다")
        
        self.logger.debug("Ubiquoss E4020 SSH 접속 시작: %s", self.device['ip'])
        
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.ssh.connect(
                hostname=self.device['ip'],
                port=int(self.device['port']),
                username=self.device['username'],
                password=self.device['password'],
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False
            )

            self.channel = self.ssh.invoke_shell(width=200, height=1000)
            self.channel.settimeout(self.timeout)
            
            self.logger.debug("SSH 셸 호출 후 초기 프롬프트 대기 시작...")
            # 초기 셸 안정화 및 프롬프트 로드 시간 증가
            time.sleep(2.5) # 기존 1초에서 2.5초로 늘림
            output = self._read_channel() # 첫번째 읽기

            # 만약 초기 출력이 비어있거나 프롬프트가 없다면 엔터키로 유도
            if not output.strip() or (not output.strip().endswith('>') and not output.strip().endswith('#')):
                self.logger.debug("초기 출력에 프롬프트 없음. 엔터키 전송 시도.")
                self.channel.send("\n")
                time.sleep(1) # 엔터 후 응답 대기
                output += self._read_channel() # 추가 출력 읽기
            
            self.log_output("SSH 초기 접속 및 프롬프트 확인 시도 후 출력", output)

            # 최종 프롬프트 확인
            if output.strip().endswith('>'):
                self.prompt_char = '>'
                self.logger.info("Ubiquoss E4020 SSH 초기 프롬프트 '%s' 확인", self.prompt_char)
                return True
            elif output.strip().endswith('#'):
                self.prompt_char = '#'
                self.logger.info("Ubiquoss E4020 SSH 초기 프롬프트 '%s' 확인 (이미 enable 모드)", self.prompt_char)
                return True
            else:
                self.logger.error("Ubiquoss E4020 SSH 초기 프롬프트를 확인할 수 없습니다. 최종 출력: %s", output)
                raise ConnectionError("Ubiquoss E4020 SSH 초기 프롬프트 확인 실패")

        except Exception as e:
            self.logger.error("Ubiquoss E4020 SSH 접속 실패: %s", e)
            if self.ssh:
                self.ssh.close()
            raise
    
    def _read_channel(self):
        """SSH 채널에서 사용 가능한 모든 데이터 읽기"""
        output = ""
        if self.channel and self.channel.recv_ready():
            while self.channel.recv_ready():
                chunk = self.channel.recv(65535)
                output += chunk.decode('utf-8', errors='ignore')
                time.sleep(0.05) # 짧은 추가 대기
        return output
    
    def enable(self):
        """특권 모드 진입"""
        self.logger.debug("Ubiquoss E4020 enable 모드 진입 시도: %s", self.device['ip'])
        if self.prompt_char == '#':
            self.logger.info("이미 특권 모드(#)입니다.")
            # 페이징 비활성화 시도 (이미 # 모드일 경우도 대비)
            self.channel.send("terminal length 0\n")
            time.sleep(0.5)
            self.log_output("terminal length 0 시도 (이미 # 모드)", self._read_channel())
            return True

        if self.prompt_char != '>':
            self.logger.error("특권 모드 진입을 위해 사용자 모드(>)여야 합니다.")
            return False
            
        self.channel.send("enable\n")
        time.sleep(0.5) # 명령어 전송 후 대기
        output = self._read_channel()
        self.log_output("enable 명령어 전송 후", output)

        if "Password:" in output or "password:" in output.lower():
            enable_password = self.device.get('enable_password', self.device.get('password')) # enable_password 없으면 기본 password 사용
            if not enable_password:
                self.logger.error("Enable 모드 진입을 위한 비밀번호가 없습니다.")
                return False
            
            self.logger.debug("Enable 비밀번호 입력 시도")
            self.channel.send(enable_password + "\n")
            time.sleep(1) # 비밀번호 입력 후 프롬프트 변경 대기
            
            # 프롬프트 확인을 위해 추가 데이터 읽기
            # clear buffer before finding prompt
            self._read_channel() # 기존 버퍼 클리어
            self.channel.send("\n") # 엔터 보내서 프롬프트 유도
            time.sleep(0.5)
            final_output = self._read_channel()
            self.log_output("Enable 비밀번호 입력 후 프롬프트 확인", final_output)

            if final_output.strip().endswith('#'):
                self.prompt_char = '#'
                self.logger.info("Enable 모드 진입 성공 (# 프롬프트 확인)")
                # 페이징 비활성화 시도
                self.channel.send("terminal length 0\n") # Ubiquoss에서 이 명령어가 동작하는지 확인 필요
                time.sleep(0.5)
                self.log_output("terminal length 0 명령어 실행 시도", self._read_channel())
                return True
            else:
                self.logger.error("Enable 모드 진입 실패: # 프롬프트를 찾을 수 없습니다. 최종 출력: %s", final_output)
                return False
        else:
            self.logger.error("Enable 모드 진입 실패: 'Password:' 프롬프트를 찾을 수 없습니다. 출력: %s", output)
            return False
    
    def send_command(self, command, timeout=None):
        """명령어 실행 (페이징 처리 포함)"""
        # 실제 timeout은 내부 폴링 로직의 타임아웃에 의해 결정됨
        
        if self.prompt_char != '#':
            self.logger.warning("명령어 실행은 특권 모드(#)에서 권장됩니다.")
            # 비-특권 모드에서도 실행은 허용하나, 결과가 다를 수 있음

        self.log_output(f"명령어 실행: {command}", "")
        self._read_channel() # 이전 출력 버퍼 비우기
        
        self.channel.send(command + "\n")
        
        full_output = ""
        # 명령어 에코를 포함하여 첫 출력을 기다림 (짧은 시간)
        time.sleep(0.5) 
        full_output += self._read_channel()

        max_pages = 50  # 무한 루프 방지
        for _ in range(max_pages):
            if "--More--" in full_output or " --More--" in full_output: # 다양한 More 형태 고려
                self.logger.debug("More 프롬프트 감지됨. 스페이스 전송.")
                self.channel.send(" ")
                
                page_output_segment = ""
                wait_start_time = time.time()
                # 다음 페이지 데이터가 나타날 때까지의 최대 대기 시간
                poll_timeout_for_next_page = 2.0 # 이전 Axgate보다 약간 길게 설정해볼 수 있음

                while not self.channel.recv_ready():
                    if (time.time() - wait_start_time) > poll_timeout_for_next_page:
                        self.logger.info("페이징: 다음 페이지 데이터 대기 시간 초과 (%ss).", poll_timeout_for_next_page)
                        break 
                    time.sleep(0.01) 
                
                if self.channel.recv_ready():
                    page_output_segment = self._read_channel()
                    full_output += page_output_segment
                
                if not page_output_segment and not self.channel.recv_ready(): 
                    # 데이터를 못 받았고, 더 받을 것도 없으면 종료될 수 있음
                    self.logger.debug("페이징: 스페이스 전송 후 추가 데이터 없음.")
                    break
            else:
                # More 프롬프트가 없으면, 추가 데이터가 있는지 잠시 더 확인
                time.sleep(0.3) # 최종 데이터 수집을 위한 짧은 대기
                full_output += self._read_channel()
                break 
        
        # 명령어 에코 및 프롬프트 제거
        lines = full_output.splitlines()
        cleaned_lines = []
        
        command_strip = command.strip()
        prompt_char_str = str(self.prompt_char) # None일 경우 대비

        for i, line in enumerate(lines):
            line_strip = line.strip()
            # 명령어 에코 제거 (첫 줄에만 해당 가능성 높음)
            if i == 0 and command_strip == line_strip:
                continue
            # 프롬프트 라인 제거 (마지막 라인일 가능성 높음)
            # 주의: 프롬프트 문자만으로 끝나는지, 아니면 "hostname#" 형태인지 확인 필요
            # 여기서는 단순하게 프롬프트 문자로 끝나는 경우만 고려
            if line_strip.endswith(prompt_char_str) and not line_strip.startswith(prompt_char_str) and len(line_strip) > len(prompt_char_str): # "hostname#"
                 # Check if it's just the prompt or part of the output
                is_prompt_line = True
                # (더 정교한 프롬프트 패턴 확인 로직 추가 가능)
                if is_prompt_line and i == len(lines) -1 : # 마지막 줄의 프롬프트만 제거 시도
                    continue

            # "--More--" 문자열 자체를 포함하는 라인 정리 (이미 스페이스로 넘겼으므로 실제 출력에선 제거)
            if "--More--" in line or " --More--" in line:
                 # --More-- 앞부분만 취하거나, 아예 라인을 버릴 수도 있음. 여기서는 일단 유지하되, 파싱 시 문제될 수 있음.
                 # 더 나은 방법은 --More-- 이전까지만 취하는 것.
                 more_idx = line.find("--More--")
                 if more_idx != -1:
                     cleaned_lines.append(line[:more_idx].rstrip())
                     continue # 다음 라인으로
            
            cleaned_lines.append(line)
            
        result = "\n".join(cleaned_lines)
        # \r 문자 제거 추가
        result = result.replace('\r', '')
        self.log_output("정리된 명령어 결과", result)
        return result
    
    def disconnect(self):
        """SSH 연결 종료"""
        self.logger.debug("Ubiquoss E4020 SSH 연결 종료 시도: %s", self.device['ip'])
        if self.channel:
            try:
                # 채널을 통해 exit 명령어 전송 (선택 사항, 이미 셸이므로 큰 의미 없을 수 있음)
                # self.channel.send("exit\\n") 
                # time.sleep(0.5)
                self.channel.close()
                self.logger.debug("SSH 채널 닫힘.")
            except Exception as e:
                self.logger.warning("SSH 채널 닫기 중 오류: %s", e)
        if self.ssh:
            try:
                self.ssh.close()
                self.logger.debug("SSH 연결 닫힘.")
            except Exception as e:
                self.logger.warning("SSH 연결 닫기 중 오류: %s", e)
        
        self.channel = None
        self.ssh = None
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write(f"\n{'='*50}\n")
                log.write(f"세션 완료 (Ubiquoss SSH)\n")
                log.write(f"{'='*50}\n\n")

@register_handler('ubiquoss', 'e4020', 'telnet')
class UbiquossE4020Handler(CustomDeviceHandler):
    """유비쿼스 E4020 장비 핸들러"""
    
    def __init__(self, device, timeout=10, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.tn = None
    
    def connect(self):
        """텔넷으로 장비에 연결"""
        if self.device['connection_type'].lower() != 'telnet':
            raise ValueError("UbiquossE4020Handler는 텔넷 연결만 지원합니다")
        
        self.logger.debug("유비쿼스 장비 Telnet 접속 시작: %s", self.device['ip'])
        
        self.tn = telnetlib.Telnet(self.device['ip'], port=self.device['port'], timeout=self.timeout)
        
        # Username 입력 - str()로 명시적 변환
        self.tn.read_until(b"Username:", timeout=10)
        self.tn.write(str(self.device['username']).encode('utf-8') + b"\n")
        time.sleep(1)
        
        # Password 입력 - str()로 명시적 변환
        self.tn.read_until(b"Password:", timeout=10)
        self.tn.write(str(self.device['password']).encode('utf-8') + b"\n")
        time.sleep(2)
        
        # 로그인 후 출력 확인
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        self.log_output("로그인 후 출력", output)
        
        return True
    
    def enable(self):
        """특권 모드 진입"""
        self.tn.write(b"enable\n")
        time.sleep(1)
        
        # Enable 비밀번호 입력 (있는 경우) - str()로 명시적 변환
        if self.device.get('enable_password'):
            self.tn.read_until(b"Password:", timeout=10)
            self.tn.write(str(self.device['enable_password']).encode('utf-8') + b"\n")
            time.sleep(2)
        
        # terminal length 0 명령어 실행 (스크롤 없이 전체 출력)
        self.tn.write(b"terminal length 0\n")
        time.sleep(1)
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        self.log_output("terminal length 0 명령어 실행 결과", output)
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 3  # 기본 타임아웃 값
        
        self.log_output(f"명령어 실행: {command}", "")
        
        self.tn.write(command.encode('utf-8') + b"\n")
        time.sleep(timeout)  # 명령어 실행 결과 기다림
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        
        # \r 문자 제거 추가
        output = output.replace('\r', '')
        
        self.log_output("출력", output)
        
        return output
    
    def disconnect(self):
        """텔넷 연결 종료"""
        if self.tn:
            self.tn.write(b"exit\n")
            self.tn.close()
            self.tn = None
            
            # 세션 로그 종료
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as log:
                    log.write(f"\n{'='*50}\n")
                    log.write(f"세션 완료\n")
                    log.write(f"{'='*50}\n\n") 
