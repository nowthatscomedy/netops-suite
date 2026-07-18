"""
Network Device Inspection Tool - Axgate Module

Axgate 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: axgate
"""

from core import telnet_compat as telnetlib
import time
import logging
import paramiko
import re
from datetime import datetime
from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)

# Axgate 장비 점검 명령어 정의
AXGATE_INSPECTION_COMMANDS = {
    'axgate': {
        'axgate': [
            'show system version',
            'show system hostname',
            'show system temperature',
            'show system fan',
            'show system uptime',
            'show resource cpu',
            'show resource memory',
            'show system power',
            'show running-config'
        ]
    }
}

# Axgate 장비 설정 백업 명령어 정의
AXGATE_BACKUP_COMMANDS = {
    'axgate': {
        'axgate': 'show running-config'
    }
}

# Axgate 장비 출력 파싱 규칙
AXGATE_PARSING_RULES = {
    'axgate': {
        'axgate': {
            'show system version': {
                'patterns': [
                    {
                        'pattern': r'OS:\s+(.+)',
                        'output_column': 'Version',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Serial:\s+(.+)',
                        'output_column': 'Serial Number',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Board:\s+(.+)',
                        'output_column': 'Model',
                        'first_match_only': True
                    }
                ]
            },
            'show system hostname': {
                'pattern': r'Hostname:\s+(.+)',
                'output_column': 'Hostname',
                'first_match_only': True
            },
            'show system temperature': {
                'patterns': [
                    {
                        'pattern': r'System:\s+(\+?[0-9.-]+\s*C)',
                        'output_column': 'System Temperature',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'CPU:\s+(\+?[0-9.-]+\s*C)',
                        'output_column': 'CPU Temperature',
                        'first_match_only': True
                    }
                ]
            },
            'show system fan': {
                'pattern': r'Chassis:\s+(\S+)',
                'output_column': 'Fan Status',
                'first_match_only': True
            },
            'show system uptime': {
                'pattern': r'Uptime:\s+(.+)',
                'output_column': 'Uptime',
                'first_match_only': True
            },
            'show resource cpu': {
                'patterns': [
                    {
                        'pattern': r'T\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\d+)',
                        'output_columns': ['CPU Total', 'CPU Used'],
                        'first_match_only': True,
                        'process': {
                            'type': 'percentage',
                            'inputs': ['CPU Used', 'CPU Total'],
                            'output_column': 'CPU Usage'
                        }
                    }
                ]
            },
            'show resource memory': {
                'patterns': [
                    {
                        'pattern': r'T\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(\d+)',
                        'output_columns': ['Memory Total', 'Memory Used'],
                        'first_match_only': True,
                        'process': {
                            'type': 'percentage',
                            'inputs': ['Memory Used', 'Memory Total'],
                            'output_column': 'Memory Usage'
                        }
                    }
                ]
            },
            'show system power': {
                'custom_parser': 'parsing_axgate_power_status',
                'output_column': 'Power Status'
            }
        }
    }
}

# --- Axgate 커스텀 파싱 함수들 ---
def parsing_axgate_power_status(output: str) -> str:
    """Axgate 'show system power' 명령어 출력 파싱"""
    # 예시 출력:
    # [Power Supply]
    # unit1: OK
    # unit2: OK
    # unit3: Not Detected (또는 다른 비정상 상태)
    warnings = []
    # [Power Supply] 헤더 이후 라인부터 실제 데이터로 간주
    data_started = False
    for line in output.splitlines():
        line_strip = line.strip()
        if not line_strip: # 빈 줄 건너뛰기
            continue
        if "[Power Supply]" in line_strip:
            data_started = True
            continue
        
        if data_started:
            # "unitX: STATUS" 형태의 라인 처리
            match = re.match(r"(unit\d+):\s*(.+)", line_strip, re.IGNORECASE)
            if match:
                unit_name = match.group(1)
                unit_status = match.group(2).upper() # 상태를 대문자로 변환하여 비교
                if unit_status != 'OK':
                    warnings.append(f"{unit_name}({unit_status})") # 상태도 함께 표시
            # elif line_strip: # 예상치 못한 형식의 라인이지만 비어있지 않으면 로깅 또는 예외처리 고려
            #     logger.warning(f"Axgate Power Status: 예상치 못한 형식의 라인 발견 - '{line_strip}'")

    if not warnings:
        # 유닛 정보가 하나도 없는데 경고도 없으면, 출력이 비정상일 수 있음
        if not data_started and not output.strip(): # 출력이 아예 없는 경우
            return "Error: No power status data found"
        elif data_started and not re.search(r"unit\d+:", output, re.IGNORECASE): # [Power Supply]는 있었지만 유닛 정보가 없는 경우
             return "Error: No power units found after header"
        return "OK"
    else:
        return "WARN: " + ", ".join(warnings)

@register_handler('axgate', 'axgate', 'telnet')
class AxgateHandler(CustomDeviceHandler):
    """Axgate 장비 핸들러"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.tn = None
    
    def connect(self):
        """텔넷으로 장비에 연결"""
        if self.device['connection_type'].lower() != 'telnet':
            raise ValueError("AxgateHandler는 텔넷 연결만 지원합니다")
        
        self.logger.debug("Axgate 장비 접속 시작: %s", self.device['ip'])
        
        self.tn = telnetlib.Telnet(self.device['ip'], port=self.device['port'], timeout=self.timeout)
        
        # Username 입력 (타임아웃 20초로 늘림)
        self.tn.read_until(b"Username:", timeout=20)
        self.tn.write(self.device['username'].encode('utf-8') + b"\n")
        time.sleep(2)  # 대기 시간 증가
        
        # Password 입력 (타임아웃 20초로 늘림)
        self.tn.read_until(b"Password:", timeout=20)
        self.tn.write(self.device['password'].encode('utf-8') + b"\n")
        time.sleep(3)  # 대기 시간 증가
        
        # 로그인 후 출력 확인
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        self.log_output("로그인 후 출력", output)
        
        return True
    
    def enable(self):
        """특권 모드 진입 - Axgate는 enable 명령어가 필요 없음"""
        # Axgate는 로그인 후 이미 특권 모드이므로 아무 작업도 수행하지 않음
        self.logger.debug("Axgate는 enable 명령어가 필요 없음: %s", self.device['ip'])
        
        # 로그에 기록
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write("\nAxgate는 enable 명령어가 필요 없음\n")
                log.write("-"*50 + "\n")
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 3  # 기본 타임아웃 값
        
        self.log_output(f"명령어 실행: {command}", "")
        
        self.tn.write(command.encode('utf-8') + b"\n")
        time.sleep(timeout)  # 명령어 실행 결과 기다림
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        
        # 문자 제거 추가
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
                    log.write(f"세션 완료 - {datetime.now()}\n")
                    log.write(f"{'='*50}\n\n")


@register_handler('axgate', 'axgate', 'ssh')
class AxgateSSHHandler(CustomDeviceHandler):
    """Axgate 장비 SSH 핸들러"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        
    def connect(self):
        """SSH로 장비에 연결"""
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("AxgateSSHHandler는 SSH 연결만 지원합니다")
        
        self.logger.debug("Axgate 장비 SSH 접속 시작: %s", self.device['ip'])
        
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # 일단 username/password를 connect에 전달 시도
            self.ssh.connect(
                self.device['ip'],
                port=int(self.device['port']),
                username=self.device['username'],
                password=self.device['password'],
                look_for_keys=False,
                allow_agent=False,
                timeout=self.timeout,
                banner_timeout=self.timeout, # 배너 타임아웃 추가
                auth_timeout=self.timeout    # 인증 타임아웃 추가
            )
            
            self.channel = self.ssh.invoke_shell(width=200, height=1000) # 터미널 크기 지정
            self.channel.settimeout(self.timeout) # 채널 타임아웃 설정
            
            time.sleep(1.5) # 셸이 안정화되고 초기 프롬프트가 로드될 시간을 조금 더 줍니다.
            
            output = self._read_channel()
            self.log_output("SSH invoke_shell 후 초기 출력", output)

            # Username 프롬프트 처리
            # Username: 프롬프트는 일반적으로 connect()에서 username을 제공하지 않거나, 
            # 서버가 키보드 인터랙티브 인증을 요구할 때 나타납니다.
            # 현재 connect()에 username/password를 모두 제공하므로, 이 프롬프트가 나타난다면 예외적인 상황입니다.
            if "Username:" in output:
                self.logger.debug("Username 프롬프트 감지됨. 사용자 이름 전송.")
                self.channel.send(self.device['username'] + "\n")
                time.sleep(1) # 사용자 이름 전송 후 응답 대기
                current_output = self._read_channel() # 사용자 이름 전송 후 추가 출력 읽기
                self.log_output("Username 전송 후 출력", current_output)
                output += current_output # 이전 출력과 합침

            # Password 프롬프트 처리
            # connect()에서 password 인증이 실패했거나, Username 입력 후 Password를 요구하는 경우
            if "Password:" in output: 
                self.logger.debug("Password 프롬프트 감지됨. 비밀번호 전송.")
                self.channel.send(self.device['password'] + "\n")
                # 비밀번호 입력 후 로그인 성공 메시지 등을 넘기기 위해 엔터 3번 입력
                self.log_output("Password 전송 완료. 엔터 3회 입력 시작", "")
                for i in range(3):
                    self.channel.send("\n")
                    time.sleep(0.2) # 각 엔터 후 짧은 대기
                    current_output_after_enter = self._read_channel()
                    self.log_output(f"엔터 {i+1}회 입력 후 출력", current_output_after_enter)
                    output += current_output_after_enter # 모든 출력을 누적
            
            # 최종 프롬프트 확인으로 로그인 성공 판단
            # 모든 출력을 다시 한번 읽어 최종 상태 확인
            time.sleep(0.5) # 최종 읽기 전 짧은 대기
            output += self._read_channel()
            self.log_output("최종 프롬프트 확인 직전 누적 출력", output)

            final_prompt_line = output.strip().splitlines()[-1] if output.strip() else ""
            if "#" in final_prompt_line or ">" in final_prompt_line:
                self.logger.info("Axgate SSH 로그인 성공. 최종 프롬프트 감지: %s", final_prompt_line)
                return True
            else:
                # 한번 더 읽어보기 (네트워크 지연 등 고려)
                time.sleep(1)
                current_output = self._read_channel()
                self.log_output("최종 프롬프트 확인을 위한 추가 읽기", current_output)
                output += current_output
                final_prompt_line = output.strip().splitlines()[-1] if output.strip() else ""
                if "#" in final_prompt_line or ">" in final_prompt_line:
                    self.logger.info("Axgate SSH 로그인 성공 (추가 읽기 후). 최종 프롬프트 감지: %s", final_prompt_line)
                    return True
                else:
                    self.logger.error("Axgate SSH 로그인 실패: 최종 프롬프트(# 또는 >)를 찾을 수 없습니다. 최종 출력 기록: %s", output)
                    raise paramiko.AuthenticationException("Failed to find final prompt after SSH operations on Axgate device.")

        except paramiko.AuthenticationException as auth_e:
            self.logger.error("Axgate SSH 인증 실패: %s", auth_e)
            if self.channel:
                self.channel.close()
            if self.ssh:
                self.ssh.close()
            raise
        except paramiko.ssh_exception.SSHException as ssh_e:
            self.logger.error("Axgate SSH 연결 실패: %s", ssh_e)
            if self.channel:
                self.channel.close()
            if self.ssh:
                self.ssh.close()
            raise ValueError(f"SSH 연결 실패: {self.device['ip']}")
        except Exception as e:
            self.logger.error("Axgate SSH 접속 중 예상치 못한 예외 발생: %s", e)
            if self.channel:
                self.channel.close()
            if self.ssh:
                self.ssh.close()
            raise
    
    def _read_channel(self):
        """SSH 채널에서 데이터 읽기"""
        output = ""
        if self.channel is None:
            return output
        
        try:
            while self.channel.recv_ready():
                output += self.channel.recv(65535).decode('utf-8', errors='ignore')
        except Exception:
            pass
        
        return output
    
    def _read_until_pattern(self, patterns, timeout=20):
        """특정 패턴이 나올 때까지 출력 읽기"""
        if self.channel is None:
            return "", -1
        
        output = ""
        pattern_index = -1
        
        end_time = time.time() + timeout
        while time.time() < end_time:
            # 채널에서 데이터를 읽을 수 있는지 확인
            if self.channel.recv_ready():
                chunk = self.channel.recv(65535).decode('utf-8', errors='ignore')
                output += chunk
                
                # 패턴 확인
                for i, pattern in enumerate(patterns):
                    if re.search(pattern, output):
                        pattern_index = i
                        return output, pattern_index
            
            time.sleep(0.1)  # CPU 사용량 줄이기 위한 짧은 대기
        
        return output, pattern_index
    
    def enable(self):
        """특권 모드 진입 - Axgate는 이미 특권 모드로 로그인됨"""
        self.logger.debug("Axgate SSH는 enable 명령어가 필요 없음: %s", self.device['ip'])
        
        # 로그에 기록
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write("\nAxgate SSH는 enable 명령어가 필요 없음\n")
                log.write("-"*50 + "\n")
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 10  # 기본 타임아웃 값
        
        if self.channel is None:
            raise ValueError("SSH 채널이 초기화되지 않았습니다")
        
        self.log_output(f"명령어 실행: {command}", "")
        
        # 버퍼 비우기
        self._read_channel() # 기존 output 변수 사용하지 않도록 수정
        
        # 명령어 전송
        self.channel.send(command + "\n")
        time.sleep(1)  # 명령어 전송 후 잠시 대기
        
        # 결과 수집 (타임아웃 2배로 설정)
        raw_output, _ = self._read_until_pattern([r"[>#]"], timeout=timeout*2)
        
        # 문자 제거 추가
        cleaned_output = raw_output.replace('\r', '')
        
        # 출력 정리 - 명령어 자체와 프롬프트 제거
        lines = cleaned_output.splitlines()
        if lines and command in lines[0]: # command.strip() in lines[0].strip() 이 더 안전할 수 있음
            lines = lines[1:]
        
        # 마지막 줄이 프롬프트인 경우 제거
        if lines and (re.search(r"[>#]$", lines[-1].strip())):
            lines = lines[:-1]
        
        result = "\n".join(lines)
        self.log_output("명령어 결과", result)
        
        return result
    
    def disconnect(self):
        """SSH 연결 종료"""
        if self.channel:
            try:
                self.channel.send("exit\n")
                time.sleep(1)
                self.channel.close()
            except Exception:
                pass
            self.channel = None
        
        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass
            self.ssh = None
        
        # 세션 로그 종료
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write(f"\n{'='*50}\n")
                log.write(f"세션 완료 - {datetime.now()}\n")
                log.write(f"{'='*50}\n\n") 
