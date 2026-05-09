"""
Network Device Inspection Tool - Base Module

기본 장비 핸들러 클래스와 핸들러 선택 함수를 제공합니다.
모든 벤더별 핸들러는 이 기본 클래스를 상속받아 구현됩니다.
"""

import os
import logging
import importlib
import pkgutil
import time
import re
import paramiko

logger = logging.getLogger(__name__)

# 핸들러 등록을 위한 레지스트리
HANDLER_REGISTRY = {}

def register_handler(vendor, os_name, conn_type):
    """핸들러 클래스를 레지스트리에 등록하는 데코레이터"""
    def decorator(cls):
        key = (vendor.lower(), os_name.lower(), conn_type.lower())
        if key in HANDLER_REGISTRY:
            logger.warning("핸들러 키 중복: %s가 이미 등록되어 있습니다. 기존 핸들러를 덮어씁니다.", key)
        HANDLER_REGISTRY[key] = cls
        logger.debug("핸들러 등록: %s -> %s", key, cls.__name__)
        return cls
    return decorator

class CustomDeviceHandler:
    """커스텀 장비 핸들러 기본 클래스"""
    
    def __init__(self, device, timeout=10, session_log_file=None):
        self.device = device
        self.timeout = timeout
        self.session_log_file = session_log_file
        self.logger = logging.getLogger(__name__)
    
    def connect(self):
        """장비에 연결"""
        raise NotImplementedError("Subclasses must implement connect method")
    
    def disconnect(self):
        """장비 연결 종료"""
        raise NotImplementedError("Subclasses must implement disconnect method")
    
    def send_command(self, command, timeout=None):
        """명령어 실행"""
        raise NotImplementedError("Subclasses must implement send_command method")
    
    def enable(self):
        """특권 모드 진입"""
        raise NotImplementedError("Subclasses must implement enable method")
    
    def log_output(self, message, output):
        """출력 로깅"""
        if self.session_log_file:
            with open(self.session_log_file, 'a', encoding='utf-8') as log:
                log.write(f"\n{message}\n")
                log.write("-"*50 + "\n")
                log.write(f"{output}\n")
                log.write("-"*50 + "\n")
    
    def get_backup_filename(self, backup_dir):
        """백업 파일명 생성"""
        return os.path.join(
            backup_dir,
            f"{self.device['ip']}_{self.device['vendor']}_{self.device['os']}.txt"
        )

class GenericParamikoHandler(CustomDeviceHandler):
    """Paramiko 기반 공용 SSH 핸들러 (handler_overrides 설정 지원)"""

    # handler_overrides에서 사용 가능한 기본값
    _DEFAULTS = {
        "enable_command": "enable",
        "disable_paging_command": "terminal length 0",
        "prompt_pattern": r"[>#]\s*$",
        "initial_delay": 1.0,
        "command_delay": 2.0,
        "read_delay": 0.2,
        "more_pattern": "--More--",
        "more_response": " ",
        "shell_width": 200,
        "shell_height": 1000,
        "skip_enable": False,
    }

    def __init__(self, device, timeout=10, session_log_file=None, handler_config: dict | None = None):
        super().__init__(device, timeout, session_log_file)
        self.ssh = None
        self.channel = None
        self.prompt = None

        # handler_overrides 설정 병합 (기본값 <- 사용자 설정)
        self._cfg = dict(self._DEFAULTS)
        if handler_config:
            for key, value in handler_config.items():
                if key in self._DEFAULTS:
                    self._cfg[key] = value

        self.prompt_re = re.compile(self._cfg["prompt_pattern"])

    def _get_cfg(self, key: str):
        """설정값 조회 헬퍼"""
        return self._cfg.get(key, self._DEFAULTS.get(key))

    def connect(self):
        if self.device.get("connection_type", "").lower() != "ssh":
            raise ValueError("GenericParamikoHandler는 SSH 연결만 지원합니다")

        self.logger.debug("GenericParamiko SSH 접속 시작: %s", self.device.get('ip'))
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
                look_for_keys=False
            )
            shell_width = int(self._get_cfg("shell_width"))
            shell_height = int(self._get_cfg("shell_height"))
            self.channel = self.ssh.invoke_shell(width=shell_width, height=shell_height)
            self.channel.settimeout(self.timeout)

            initial_delay = float(self._get_cfg("initial_delay"))
            time.sleep(initial_delay)
            output = self._read_channel()
            self.log_output("초기 출력", output)
            self._update_prompt(output)
            if not self.prompt:
                self.channel.send("\n")
                time.sleep(0.5)
                output = self._read_channel()
                self.log_output("프롬프트 재확인", output)
                self._update_prompt(output)
            if not self.prompt:
                raise ConnectionError("프롬프트를 찾을 수 없습니다.")
            return True
        except Exception as e:
            self.logger.error("GenericParamiko SSH 접속 실패: %s", e)
            if self.ssh:
                self.ssh.close()
            raise

    def _read_channel(self):
        output = ""
        read_delay = float(self._get_cfg("read_delay"))
        time.sleep(read_delay)
        if self.channel and self.channel.recv_ready():
            while self.channel.recv_ready():
                output += self.channel.recv(65535).decode("utf-8", "ignore")
                time.sleep(0.1)
        return output

    def _update_prompt(self, output: str) -> None:
        if not output:
            return
        for line in reversed(output.splitlines()):
            if self.prompt_re.search(line):
                self.prompt = line.strip()
                return

    def enable(self):
        if not self.channel:
            raise ConnectionError("SSH 채널이 연결되지 않았습니다.")

        # skip_enable이 true면 enable 과정 전체 건너뛰기
        if self._get_cfg("skip_enable"):
            self.logger.debug("skip_enable 설정으로 enable 과정을 건너뜁니다.")
            disable_paging = str(self._get_cfg("disable_paging_command")).strip()
            if disable_paging:
                self.channel.send(disable_paging + "\n")
                time.sleep(0.8)
                output = self._read_channel()
                self.log_output(f"{disable_paging} 명령어 후", output)
            return

        self.channel.send("\n")
        time.sleep(0.5)
        output = self._read_channel()
        self._update_prompt(output)
        if self.prompt and "#" in self.prompt:
            disable_paging = str(self._get_cfg("disable_paging_command")).strip()
            if disable_paging:
                self.channel.send(disable_paging + "\n")
                time.sleep(0.8)
                output = self._read_channel()
                self.log_output(f"{disable_paging} 명령어 후", output)
            return

        enable_cmd = str(self._get_cfg("enable_command")).strip()
        if self.prompt and ">" in self.prompt and enable_cmd:
            self.channel.send(enable_cmd + "\n")
            time.sleep(0.8)
            output = self._read_channel()
            self.log_output(f"{enable_cmd} 명령어 후", output)
            if "Password" in output or "password" in output:
                enable_password = self.device.get("enable_password") or self.device.get("password")
                if enable_password:
                    self.channel.send(str(enable_password) + "\n")
                    time.sleep(0.8)
                    output = self._read_channel()
                    self.log_output("enable 비밀번호 입력 후", output)
            self._update_prompt(output)

        disable_paging = str(self._get_cfg("disable_paging_command")).strip()
        if disable_paging:
            self.channel.send(disable_paging + "\n")
            time.sleep(0.8)
            output = self._read_channel()
            self.log_output(f"{disable_paging} 명령어 후", output)

    def send_command(self, command, timeout=None):
        if not self.channel:
            raise ConnectionError("SSH 채널이 연결되지 않았습니다.")
        timeout = timeout or 10
        self.log_output(f"명령어 실행: {command}", "")
        self._read_channel()
        self.channel.send(command + "\n")

        command_delay = float(self._get_cfg("command_delay"))
        more_pattern = str(self._get_cfg("more_pattern"))
        more_response = str(self._get_cfg("more_response"))
        read_delay = float(self._get_cfg("read_delay"))

        full_output = ""
        start = time.time()
        time.sleep(command_delay)
        while time.time() - start < timeout:
            chunk = self._read_channel()
            if chunk:
                full_output += chunk
                if more_pattern and more_pattern in chunk:
                    self.channel.send(more_response)
                    time.sleep(0.3)
                    continue
            if self.prompt and full_output.strip().endswith(self.prompt):
                break
            if self.prompt_re.search(full_output.splitlines()[-1] if full_output else ""):
                break
            time.sleep(read_delay)

        lines = full_output.splitlines()
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        if lines and self.prompt and self.prompt in lines[-1]:
            lines = lines[:-1]
        cleaned_output = "\n".join(lines).strip()
        self.log_output("정리된 명령어 결과", cleaned_output)
        return cleaned_output

    def disconnect(self):
        if self.channel:
            self.channel.close()
        if self.ssh:
            self.ssh.close()
        self.channel = None
        self.ssh = None

def _load_handlers():
    """
    vendors 패키지 내의 모든 모듈을 임포트하여 핸들러가 레지스트리에 등록되도록 합니다.
    이 함수는 get_custom_handler가 처음 호출될 때 한 번만 실행됩니다.
    """
    pkg_path = os.path.dirname(__file__)
    pkg_name = os.path.basename(pkg_path)
    for _, name, _ in pkgutil.iter_modules([pkg_path]):
        importlib.import_module(f'.{name}', pkg_name)

_load_handlers() # 이 파일을 임포트하는 시점에 모든 핸들러 로드

def get_custom_handler(device, timeout=10, session_log_file=None):
    """장비 유형에 맞는 커스텀 핸들러 반환"""
    vendor = device.get('vendor', '').lower()
    model = device.get('os', '').lower()
    connection_type = device.get('connection_type', '').lower()
    
    key = (vendor, model, connection_type)
    handler_class = HANDLER_REGISTRY.get(key)
    
    if handler_class:
        logger.debug("핸들러 찾음: %s -> %s", key, handler_class.__name__)
        return handler_class(device, timeout, session_log_file)
    
    # 레거시 cisco 핸들러 같이 특정 os가 아닌 경우도 찾아보기
    key_generic_os = (vendor, '*', connection_type)
    handler_class = HANDLER_REGISTRY.get(key_generic_os)
    if handler_class:
        logger.debug("핸들러 찾음 (Generic OS): %s -> %s", key_generic_os, handler_class.__name__)
        return handler_class(device, timeout, session_log_file)
        
    logger.debug("커스텀 핸들러를 찾을 수 없음: %s", key)
    return None 