"""
Network Device Inspection Tool - NexG Module

NexG 장비의 명령어, 파싱 규칙, 핸들러 클래스를 제공합니다.
지원 OS: vforce
"""

from core import telnet_compat as telnetlib
import time
import logging
import paramiko
import socket
from vendors.base import CustomDeviceHandler, register_handler

logger = logging.getLogger(__name__)

# NexG 장비 점검 명령어 정의
NEXG_INSPECTION_COMMANDS = {
    'nexg': {
        'vforce': [
            'show version',
            'show running-config'
        ]
    }
}

# NexG 장비 설정 백업 명령어 정의
NEXG_BACKUP_COMMANDS = {
    'nexg': {
        'vforce': 'show running-config'
    }
}

# NexG 장비 출력 파싱 규칙
NEXG_PARSING_RULES = {
    'nexg': {
        'vforce': {
            'show version': {
                'patterns': [
                    {
                        'pattern': r'Version\s+:\s+([\d\.]+)',
                        'output_column': 'Version'
                    },
                    {
                        'pattern': r'Hostname\s+:\s+(\S+)',
                        'output_column': 'Hostname'
                    },
                    {
                        'pattern': r'Uptime\s+:\s+(.*)',
                        'output_column': 'Uptime'
                    },
                    {
                        'pattern': r'Model\s+:\s+(\S+)',
                        'output_column': 'Model'
                    },
                    {
                        'pattern': r'Serial Number\s+:\s+(\S+)',
                        'output_column': 'Serial Number'
                    }
                ]
            }
        }
    }
}

@register_handler('nexg', 'vforce', 'ssh')
class VForceSSHHandler(CustomDeviceHandler):
    """NexG VForce SSH 장비 핸들러 (Axgate와 유사한 접속 방식)"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.channel = None
    
    def connect(self):
        """SSH로 장비에 연결"""
        if self.device['connection_type'].lower() != 'ssh':
            raise ValueError("VForceSSHHandler는 SSH 연결만 지원합니다")
        
        self.logger.debug("VForce 장비 SSH 접속 시작: %s", self.device['ip'])

        # 디버깅을 위한 기본 연결 정보 출력
        self.logger.debug("연결 정보 - IP: %s, PORT: %s, USER: %s", self.device['ip'], self.device['port'], self.device['username'])
        
        try:
            # 소켓 및 Transport 설정 (사용자 이름 직접 처리)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.device['ip'], int(self.device['port'])))
            
            # Transport 설정
            transport = paramiko.Transport(sock)
            transport.start_client()
            
            # 사용자 이름 전달 (자동 처리)
            transport.auth_none(str(self.device['username']))
            
            # 채널 생성
            self.channel = transport.open_session()
            self.channel.get_pty()
            self.channel.invoke_shell()
            self.channel.settimeout(self.timeout)
            
            # 초기 응답 확인 (비밀번호 프롬프트 대기)
            time.sleep(2)
            output = self._read_channel()
            self.log_output("초기 응답", output)
            
            # 비밀번호 프롬프트 확인 및 입력
            if "Password:" in output or "password:" in output:
                self.logger.debug("비밀번호 프롬프트 확인됨")
                self.channel.send(str(self.device['password']) + "\n")
                time.sleep(2)
            else:
                self.logger.warning("비밀번호 프롬프트가 없습니다. 직접 비밀번호 전송 시도")
                self.channel.send(str(self.device['password']) + "\n")
                time.sleep(2)
            
            # 로그인 성공 확인
            output = self._read_channel()
            self.log_output("비밀번호 입력 후 응답", output)
            
            # 로그인 성공 여부 확인 (프롬프트 확인)
            if "#" in output or ">" in output:
                self.logger.debug("로그인 성공: %s", self.device['ip'])
                return True
            else:
                # 추가 출력 확인
                time.sleep(2)
                extra_output = self._read_channel()
                self.log_output("추가 대기 후 응답", extra_output)
                
                if "#" in extra_output or ">" in extra_output:
                    self.logger.debug("추가 대기 후 로그인 성공: %s", self.device['ip'])
                    return True
                else:
                    # 로그인 성공 여부 불확실하지만 계속 진행
                    self.logger.info("프롬프트를 찾을 수 없지만 계속 진행합니다")
                    return True
            
        except Exception as e:
            self.logger.error("VForce SSH 연결 실패: %s", e)
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as log:
                    log.write(f"\nSSH 연결 실패: {str(e)}\n")
                    log.write("-"*50 + "\n")
            raise
    
    def _read_channel(self):
        """채널에서 데이터를 읽어 문자열로 반환합니다."""
        output = ""
        try:
            if self.channel.recv_ready():
                while self.channel.recv_ready():
                    chunk = self.channel.recv(4096)
                    output += chunk.decode('utf-8', 'ignore')
                    time.sleep(0.1)
        except Exception as e:
            self.logger.warning("채널 읽기 중 오류: %s", e)
        return output
    
    def enable(self):
        """특권 모드 진입 - VForce는 enable 명령어가 필요할 수 있음"""
        self.logger.debug("VForce enable 모드 확인: %s", self.device['ip'])
        
        # 로그에 기록
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write("\nVForce enable 모드 확인\n")
                log.write("-"*50 + "\n")
                
        # 현재 프롬프트 확인
        try:
            self.channel.send("\n")
            time.sleep(0.5)
            output = self._read_channel()
            
            # '>' 프롬프트이면 enable 모드로 전환 시도
            if ">" in output and "#" not in output:
                self.logger.debug("일반 모드(>)에서 특권 모드(#)로 전환 시도")
                self.channel.send("enable\n")
                time.sleep(1)
                
                # enable 비밀번호 프롬프트 확인
                output = self._read_channel()
                if "Password:" in output:
                    # enable 비밀번호 전송
                    if self.device.get('enable_password'):
                        enable_pwd = self.device['enable_password']
                    else:
                        enable_pwd = self.device['password']
                    
                    self.channel.send(str(enable_pwd) + "\n")
                    time.sleep(1)
                    
                    # 프롬프트 확인
                    output = self._read_channel()
                    if "#" in output:
                        self.logger.debug("특권 모드 전환 성공")
                    else:
                        self.logger.warning("특권 모드 전환 실패")
        except Exception as e:
            self.logger.warning("특권 모드 전환 확인 중 오류: %s", e)
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 5  # SSH는 기본 타임아웃을 조금 더 길게 설정
        
        self.log_output(f"명령어 실행: {command}", "")
        
        try:
            # 채널 초기화 (기존 출력 비우기)
            self._read_channel()
            
            # 명령어 전송
            self.channel.send(command + "\n")
            
            # 명령어 실행 결과 대기
            time.sleep(timeout)
            
            # 출력 수집
            output = self._read_channel()
            
            # 명령어 및 프롬프트 제거 로직
            lines = output.splitlines()
            
            # 'show running-config'와 같은 페이징 명령어를 위한 처리
            if '--More--' in output:
                self.logger.debug("페이징 명령어 감지됨, 전체 출력 수집")
                # 스페이스바를 전송하여 다음 페이지 요청
                full_output = output
                max_pages = 50  # 안전을 위한 최대 페이지 수 제한
                
                for _ in range(max_pages):
                    if '--More--' in full_output:
                        # 스페이스바 전송
                        self.channel.send(" ")
                        time.sleep(1)
                        page_output = self._read_channel()
                        full_output += page_output
                    else:
                        break
                
                # 전체 출력을 라인 단위로 분할
                lines = full_output.splitlines()
            
            # 출력 처리
            if len(lines) > 0:
                # 첫 줄은 명령어 자체이거나 빈 줄일 수 있으므로 검사
                if command.strip() in lines[0]:
                    lines = lines[1:]  # 명령어 줄 제거
                
                # 마지막 줄이 프롬프트인지 확인
                if lines and ('#' in lines[-1] or '>' in lines[-1]):
                    lines = lines[:-1]  # 프롬프트 줄 제거
                
                # '--More--' 표시가 있는 줄 정리
                clean_lines = []
                for line in lines:
                    # '--More--' 제거하고 해당 줄의 나머지 부분만 유지
                    if '--More--' in line:
                        clean_line = line.split('--More--')[0].strip()
                        if clean_line:  # 빈 줄이 아니면 추가
                            clean_lines.append(clean_line)
                    else:
                        clean_lines.append(line)
                
                # 정리된 출력 합치기
                cleaned_output = "\n".join(clean_lines)
            else:
                cleaned_output = ""
            
            self.log_output("출력", cleaned_output)
            
            return cleaned_output
        except Exception as e:
            self.logger.error("명령어 실행 중 오류: %s", e)
            self.log_output("명령어 실행 오류", str(e))
            return f"명령어 실행 오류: {str(e)}"
    
    def disconnect(self):
        """SSH 연결 종료"""
        try:
            if self.channel:
                self.channel.close()
                
            # Transport 종료
            if self.channel and self.channel.get_transport():
                self.channel.get_transport().close()
                
            # 세션 로그 종료
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as log:
                    log.write(f"\n{'='*50}\n")
                    log.write(f"세션 완료\n")
                    log.write(f"{'='*50}\n\n")
        except Exception as e:
            self.logger.warning("연결 종료 중 오류: %s", e)


@register_handler('nexg', 'vforce', 'telnet')
class VForceTelnetHandler(CustomDeviceHandler):
    """NexG VForce 장비 Telnet 핸들러"""
    
    def __init__(self, device, timeout=30, session_log_file=None):
        super().__init__(device, timeout, session_log_file)
        self.tn = None
    
    def connect(self):
        """텔넷으로 장비에 연결"""
        if self.device['connection_type'].lower() != 'telnet':
            raise ValueError("VForceTelnetHandler는 텔넷 연결만 지원합니다")
        
        self.logger.debug("VForce 장비 Telnet 접속 시작: %s", self.device['ip'])
        
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
        """특권 모드 진입 - NexG VForce도 필요할 수 있음"""
        self.logger.debug("VForce enable 모드 확인: %s", self.device['ip'])
        
        # 현재 프롬프트 확인
        self.tn.write(b"\n")
        time.sleep(1)
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        
        # '>' 프롬프트이면 enable 모드로 전환 시도
        if ">" in output and "#" not in output:
            self.logger.debug("일반 모드(>)에서 특권 모드(#)로 전환 시도")
            self.tn.write(b"enable\n")
            time.sleep(1)
            
            # enable 비밀번호 입력 (있는 경우)
            output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
            if "Password:" in output:
                if self.device.get('enable_password'):
                    self.tn.write(self.device['enable_password'].encode('utf-8') + b"\n")
                else:
                    self.tn.write(self.device['password'].encode('utf-8') + b"\n")
                time.sleep(2)
        
        # 로그에 기록
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write("\nVForce enable 모드 확인 완료\n")
                log.write("-"*50 + "\n")
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        if timeout is None:
            timeout = 3  # 기본 타임아웃 값
        
        self.log_output(f"명령어 실행: {command}", "")
        
        self.tn.write(command.encode('utf-8') + b"\n")
        time.sleep(timeout)  # 명령어 실행 결과 기다림
        
        # 출력 수집
        output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
        
        # 'More' 혹은 '--More--' 프롬프트 처리
        full_output = output
        max_pages = 50  # 안전을 위한 최대 페이지 수 제한
        
        for _ in range(max_pages):
            if '--More--' in full_output or ' --More-- ' in full_output:
                # 스페이스바 전송하여 다음 페이지 요청
                self.tn.write(b" ")
                time.sleep(1)
                page_output = self.tn.read_very_eager().decode('utf-8', errors='ignore')
                full_output += page_output
            else:
                break
        
        # 출력 처리 (명령어 줄과 프롬프트 줄 제거)
        lines = full_output.splitlines()
        
        # 출력 처리
        if len(lines) > 1:
            # 첫 줄은 명령어 자체일 가능성이 높음
            if command.strip() in lines[0]:
                lines = lines[1:]
            
            # 마지막 줄은 프롬프트일 가능성이 높음
            if lines and ('#' in lines[-1] or '>' in lines[-1]):
                lines = lines[:-1]
            
            # '--More--' 표시가 있는 줄 정리
            clean_lines = []
            for line in lines:
                # '--More--' 제거하고 해당 줄의 나머지 부분만 유지
                if '--More--' in line or ' --More-- ' in line:
                    clean_line = line.split('--More--')[0].strip()
                    if not clean_line and ' --More-- ' in line:
                        clean_line = line.split(' --More-- ')[0].strip()
                    if clean_line:  # 빈 줄이 아니면 추가
                        clean_lines.append(clean_line)
                else:
                    clean_lines.append(line)
            
            # 정리된 출력 합치기
            cleaned_output = "\n".join(clean_lines)
        else:
            cleaned_output = full_output
        
        self.log_output("출력", cleaned_output)
        
        return cleaned_output
    
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
