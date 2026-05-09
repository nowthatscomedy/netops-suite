"""
Network Device Inspection Tool - Alcatel-Lucent Module

Alcatel-Lucent 장비의 명령어, 파싱 규칙, 파싱 함수, 핸들러 클래스를 제공합니다.
지원 OS: aos6, aos8
"""

import re
import time
import logging
import traceback
import paramiko
from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)

# Alcatel-Lucent 장비 점검 명령어 정의
ALCATEL_LUCENT_INSPECTION_COMMANDS = {
    'alcatel-lucent': {
        'aos6': [
            'show configuration snapshot',
            'show temperature',
            'show fan',
            'show power',
            'show system',
            'show stack status',
            'show health all cpu',
            'show health all memory',
            'show chassis'
        ],
        'aos8': [
            'show configuration snapshot',
            'show temperature',
            'show fan',
            'show power',
            'show system',
            'show stack status',
            'show health all cpu',
            'show health all memory',
            'show chassis'
        ]
    }
}

# Alcatel-Lucent 장비 설정 백업 명령어 정의
ALCATEL_LUCENT_BACKUP_COMMANDS = {
    'alcatel-lucent': {
        'aos6': 'show configuration snapshot',
        'aos8': 'show configuration snapshot'
    }
}

# Alcatel-Lucent 파싱 함수들
def parsing_alcatel_hostname(output):
    """Alcatel-Lucent 장비의 호스트네임 파싱"""
    hostname = re.search(r'session prompt default "(\S+)>"', output)
    if hostname:
        result = hostname.group(1)
    else:
        result = ""
    return result

def parsing_alcatel_temperature(output):
    """Alcatel-Lucent 장비의 온도 정보 파싱"""
    lines = output.split("\n")
    chassis_numbers = []
    found_temp_info = False
    for line in lines:
        if "Temperature for chassis" in line:
            found_temp_info = True
            chassis_number = line.split()[-1]
        elif "Temperature Status" in line:
            found_temp_info = True
            if "OVER THRESHOLD" in line:
                chassis_numbers.append(chassis_number)

    if not found_temp_info:
        return ""

    if len(chassis_numbers) > 0:
        str_list = 'Check: ' + ', '.join(str(num) for num in chassis_numbers)
        result = str_list
    else:
        result = "OK"
    return result

def parsing_alcatel_fan(output):
    """Alcatel-Lucent 장비의 팬 상태 파싱"""
    lines = output.split("\n")
    not_running_fans = []
    found_fan_info = False

    for line in lines:
        try:
            line_data = line.split()
            if len(line_data) < 3:
                continue
            found_fan_info = True
            chassis = line_data[0]
            fan = line_data[1]
            status = line_data[2]

            if status == "Not" and line_data[3] == "Running":
                not_running_fans.append(chassis + "-" + fan)
        except IndexError:
            pass
    
    if not found_fan_info:
        return ""
    
    if len(not_running_fans) > 0:
        result = 'Check: ' + ", ".join(str(num) for num in not_running_fans)
    else:
        result = 'OK'
    return result

def parsing_alcatel_power(output):
    """Alcatel-Lucent 장비의 전원 상태 파싱"""
    lines = output.split("\n")
    powers = []
    found_power_info = False

    for line in lines:
        try:
            line_output = line.split()
            if len(line_output) < 5:
                continue
            found_power_info = True
            slot = line_output[0]
            power = line_output[1]
            status = line_output[4]
            if status == "DOWN":
                powers.append(slot + "-" + power)
        except IndexError:
            pass

    if not found_power_info:
        return ""
    
    if len(powers) > 0:
        result = 'Check: ' + ", ".join(str(num) for num in powers)
    else:
        result = 'OK'
    return result

def parsing_alcatel_uptime(output):
    """Alcatel-Lucent 장비의 업타임 파싱"""
    uptime = re.search(r'\s+Up\s+Time:\s+(\S+\s\S+\s\S+\s\S+)', output)
    if uptime:
        result = uptime.group(1)
    else:
        result = ""
    return result

def parsing_alcatel_version(output):
    """Alcatel-Lucent 장비의 버전 정보 파싱"""
    lines = output.strip().split('\n')
    result = "Unknown"

    try:
        for line in lines:
            words = line.split()
            if len(words) >= 2:
                result = words[-5] + ' ' + words[-4].replace(',', '')
                break
    except IndexError:
        pass
    return result

def parsing_alcatel_stack(output):
    """Alcatel-Lucent 장비의 스택 상태 파싱"""
    if "Redundant cable status  : not present" in output:
        return "not present"
    elif "Redundant cable status  : present" in output:
        return "present"
    else:
        return "Unknown"
    
def parsing_alcatel_cpu(output):
    """Alcatel-Lucent 장비의 CPU 사용률 파싱"""
    lines = output.strip().split('\n')
    cpu_usage = 0
    parsed_successfully = False

    for line in lines: 
        values = line.split()
        try:
            max_value = int(values[-2])
            if not parsed_successfully or max_value > cpu_usage:
                cpu_usage = max_value
            parsed_successfully = True
        except (IndexError, ValueError):
            pass
    
    if parsed_successfully:
        return cpu_usage
    else:
        return ""

def parsing_alcatel_memory(output):
    """Alcatel-Lucent 장비의 메모리 사용률 파싱"""
    lines = output.strip().split('\n')
    memory_usage = 0
    parsed_successfully = False

    for line in lines: 
        values = line.split()
        try:
            max_value = int(values[-2])
            if not parsed_successfully or max_value > memory_usage:
                memory_usage = max_value
            parsed_successfully = True
        except (IndexError, ValueError):
            pass
    
    if parsed_successfully:
        return memory_usage
    else:
        return ""

# Alcatel-Lucent 장비 출력 파싱 규칙
ALCATEL_LUCENT_PARSING_RULES = {
    'alcatel-lucent': {
        'aos6': {
            'show configuration snapshot': {
                'pattern': r'session prompt default "(\S+)>"',
                'output_column': 'Hostname',
                'first_match_only': True
            },
            'show temperature': {
                'custom_parser': 'parsing_alcatel_temperature',
                'output_column': 'Temperature'
            },
            'show fan': {
                'custom_parser': 'parsing_alcatel_fan',
                'output_column': 'Fan Status'
            },
            'show power': {
                'custom_parser': 'parsing_alcatel_power',
                'output_column': 'Power Status'
            },
            'show system': {
                'patterns': [
                    {
                        'pattern': r'\s+Up\s+Time:\s+(\S+\s\S+\s\S+\s\S+)',
                        'output_column': 'Uptime',
                        'first_match_only': True
                    },
                    {
                        'custom_parser': 'parsing_alcatel_version',
                        'output_column': 'Version'
                    }
                ]
            },
            'show stack status': {
                'pattern': r'Redundant cable status\s+:\s+(\S+)',
                'output_column': 'Stack Status',
                'first_match_only': True
            },
            'show health all cpu': {
                'custom_parser': 'parsing_alcatel_cpu',
                'output_column': 'CPU Usage'
            },
            'show health all memory': {
                'custom_parser': 'parsing_alcatel_memory',
                'output_column': 'Memory Usage'
            },
            'show chassis': {
                'patterns': [
                    {
                        'pattern': r'Model Name:\s+([^,]+),?',
                        'output_column': 'Model',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Serial Number:\s+([^,]+),?',
                        'output_column': 'Serial Number'
                    }
                ]
            }
        },
        'aos8': {
            'show configuration snapshot': {
                'pattern': r'session prompt default "(\S+)>"',
                'output_column': 'Hostname',
                'first_match_only': True
            },
            'show temperature': {
                'custom_parser': 'parsing_alcatel_temperature',
                'output_column': 'Temperature'
            },
            'show fan': {
                'custom_parser': 'parsing_alcatel_fan',
                'output_column': 'Fan Status'
            },
            'show power': {
                'custom_parser': 'parsing_alcatel_power',
                'output_column': 'Power Status'
            },
            'show system': {
                'patterns': [
                    {
                        'pattern': r'\s+Up\s+Time:\s+(\S+\s\S+\s\S+\s\S+)',
                        'output_column': 'Uptime',
                        'first_match_only': True
                    },
                    {
                        'custom_parser': 'parsing_alcatel_version',
                        'output_column': 'Version'
                    }
                ]
            },
            'show stack status': {
                'pattern': r'Redundant cable status\s+:\s+(\S+)',
                'output_column': 'Stack Status',
                'first_match_only': True
            },
            'show health all cpu': {
                'custom_parser': 'parsing_alcatel_cpu',
                'output_column': 'CPU Usage'
            },
            'show health all memory': {
                'custom_parser': 'parsing_alcatel_memory',
                'output_column': 'Memory Usage'
            },
            'show chassis': {
                'patterns': [
                    {
                        'pattern': r'Model Name:\s+([^,]+),?',
                        'output_column': 'Model',
                        'first_match_only': True
                    },
                    {
                        'pattern': r'Serial Number:\s+([^,]+),?',
                        'output_column': 'Serial Number'
                    }
                ]
            }
        }
    }
}

@register_handler('alcatel-lucent', 'aos6', 'ssh')
@register_handler('alcatel-lucent', 'aos8', 'ssh')
class AlcatelLucentHandler(CustomDeviceHandler):
    """Alcatel-Lucent 장비 핸들러"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt = None
    
    def connect(self):
        """SSH로 장비에 연결"""
        self.logger.debug("Alcatel-Lucent 장비 SSH 접속 시작: %s", self.device['ip'])
        
        try:
            # SSH 클라이언트 초기화
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # 연결 설정
            self.ssh.connect(
                hostname=self.device['ip'],
                username=self.device['username'],
                password=self.device['password'],
                port=self.device['port'],
                timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False
            )
            
            # 셸 요청
            self.channel = self.ssh.invoke_shell(width=160, height=1000)
            self.channel.settimeout(self.timeout)
            
            # 초기 출력 처리
            time.sleep(3)
            output = self._read_channel()
            self.log_output("초기 출력", output)
            
            # 프롬프트 확인
            if ">" in output or "#" in output:
                last_line = output.splitlines()[-1] if output.splitlines() else ""
                self.prompt = last_line.strip()
                self.logger.debug("프롬프트 설정: %s", self.prompt)
            
            # 로그인 성공 확인
            if ">" in output or "#" in output:
                self.logger.debug("Alcatel-Lucent SSH 접속 성공: %s", self.device['ip'])
                return True
            else:
                self.logger.warning("Alcatel-Lucent SSH 접속 상태 불명확: %s", self.device['ip'])
                return False
            
        except Exception as e:
            self.logger.error("Alcatel-Lucent SSH 접속 실패: %s", e)
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as log:
                    log.write(f"\n접속 실패: {str(e)}\n")
                    log.write(traceback.format_exc())
            
            if self.ssh:
                self.ssh.close()
                self.ssh = None
            
            raise
    
    def _read_channel(self):
        """채널에서 출력 읽기"""
        output = ""
        if self.channel:
            while self.channel.recv_ready():
                chunk = self.channel.recv(4096)
                output += chunk.decode('utf-8', errors='ignore')
                if not self.channel.recv_ready():
                    time.sleep(0.1)  # 더 데이터가 오는지 짧게 대기
        return output
    
    def enable(self):
        """특권 모드 진입 - Alcatel은 로그인 후 특별한 enable 명령 필요 없음"""
        self.logger.debug("Alcatel-Lucent는 별도의 enable 명령이 필요 없음: %s", self.device['ip'])
        
        return True
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 5
        
        self.log_output(f"명령어 실행: {command}", "")
        
        try:
            # 명령어 전송
            self.channel.send(command + "\n")
            
            # 충분한 시간 대기
            time.sleep(timeout)
            
            # 결과 읽기
            output = self._read_channel()
            
            # 명령어 자체와 프롬프트 제거
            lines = output.splitlines()
            clean_lines = []
            
            # 명령어 라인 건너뛰기
            skip_first = True
            for line in lines:
                if skip_first:
                    if command in line:
                        skip_first = False
                        continue
                    else:
                        skip_first = False
                
                # 마지막 프롬프트 라인 제외
                if ">" in line or "#" in line:
                    if line.strip() == self.prompt:
                        continue
                
                clean_lines.append(line)
            
            # '--More--' 처리 로직 제거됨
            
            result = "\n".join(clean_lines) # 기존 clean_lines를 바로 사용
            self.log_output("명령어 결과", result)
            
            return result
            
        except Exception as e:
            self.logger.error("명령어 실행 실패 (%s): %s", command, e)
            return f"Error executing command: {str(e)}"
    
    def disconnect(self):
        """연결 종료"""
        if self.channel:
            try:
                self.channel.close()
            except:
                pass
        
        if self.ssh:
            try:
                self.ssh.close()
            except:
                pass
            
        self.channel = None
        self.ssh = None
        
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write(f"\n{'='*50}\n")
                log.write(f"세션 종료\n")
                log.write(f"{'='*50}\n") 