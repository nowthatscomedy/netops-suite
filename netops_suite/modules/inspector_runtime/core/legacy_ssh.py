"""Process-isolated SSH compatibility without persistent host keys."""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading



class LegacySSHError(RuntimeError):
    """A compatibility failure which must not trigger automatic retries."""


def legacy_requested(device: dict) -> bool:
    value = device.get("legacy_ssh", "")
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    text = str(value).strip().lower()
    if text in {"", "false", "0", "0.0", "no"}:
        return False
    if text in {"true", "1", "1.0", "yes"}:
        return True
    raise LegacySSHError("legacy_ssh는 TRUE 또는 FALSE로 입력하세요.")


def validate_legacy_device(device: dict) -> bool:
    if not legacy_requested(device):
        return False
    if str(device.get("connection_type", "")).strip().lower() != "ssh":
        raise LegacySSHError("레거시 SSH는 SSH 연결에서만 사용할 수 있습니다.")
    fingerprint = optional_fingerprint(device)
    if fingerprint and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise LegacySSHError("ssh_host_key_sha256에 확인된 SHA256 host key 지문을 입력하세요.")
    return True


def optional_fingerprint(device: dict) -> str:
    value = device.get("ssh_host_key_sha256", "")
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def automatic_legacy_allowed(device: dict) -> bool:
    value = str(device.get("legacy_ssh", "")).strip().lower()
    return str(device.get("connection_type", "")).strip().lower() == "ssh" and value not in {"false", "0", "0.0", "no"}


def legacy_python() -> Path:
    configured = os.environ.get("NETOPS_LEGACY_SSH_PYTHON")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise LegacySSHError("NETOPS_LEGACY_SSH_PYTHON은 절대 경로여야 합니다.")
    else:
        root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[4]
        path = root / ".runtime" / "legacy-ssh" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_file():
        raise LegacySSHError("레거시 SSH 런타임이 없습니다. scripts/setup_legacy_ssh.py를 실행하세요.")
    return path


class LegacySSHClient:
    """Small SSHClient/channel adapter. Credentials only cross private pipes."""

    def __init__(self, device: dict, *, automatic: bool = False):
        if not validate_legacy_device(device) and not (automatic and automatic_legacy_allowed(device)):
            raise LegacySSHError("레거시 SSH를 명시적으로 활성화해야 합니다.")
        self.fingerprint = optional_fingerprint(device)
        self.process = None
        self.responses = queue.Queue()
        self.timeout = 30.0

    def _read_responses(self, process):
        try:
            for line in process.stdout:
                self.responses.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            self.responses.put(None)

    def _call(self, operation, **args):
        try:
            self.process.stdin.write(json.dumps({"op": operation, **args}) + "\n")
            self.process.stdin.flush()
            response = self.responses.get(timeout=self.timeout + 5)
            if response is None:
                raise LegacySSHError("레거시 SSH helper가 종료되었습니다.")
            if "error" in response:
                raise LegacySSHError(response["error"])
            return response.get("result")
        except LegacySSHError:
            self.close()
            raise
        except (OSError, ValueError, queue.Empty) as exc:
            self.close()
            raise LegacySSHError("레거시 SSH helper 응답 실패 또는 시간 초과") from exc

    def connect(self, **kwargs):
        # Live socket/key objects cannot safely cross the process boundary.
        if kwargs.get("sock") is not None or kwargs.get("pkey") is not None:
            raise LegacySSHError("레거시 SSH는 외부 소켓 또는 메모리 개인 키 연결을 지원하지 않습니다.")
        self.timeout = float(kwargs.get("timeout", 30))
        self.process = subprocess.Popen(
            [str(legacy_python()), "-I", "-u", str(Path(__file__).with_name("legacy_ssh_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._read_responses, args=(self.process,), daemon=True).start()
        try:
            self.host_key_type = self._call("connect", fingerprint=self.fingerprint, **kwargs)
        except Exception:
            self.close()
            raise

    def invoke_shell(self, **kwargs):
        self._call("shell", **kwargs)
        return self

    def settimeout(self, timeout):
        self.timeout = float(timeout)
        self._call("timeout", value=self.timeout)

    def recv_ready(self):
        return self._call("ready")

    def recv(self, size):
        return base64.b64decode(self._call("recv", size=size))

    def send(self, value):
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return self._call("send", value=base64.b64encode(data).decode("ascii"))

    def sendall(self, value):
        self.send(value)

    @property
    def transport(self):
        return self

    def get_transport(self):
        return self

    def is_active(self):
        return self.process is not None and bool(self._call("active"))

    def set_keepalive(self, interval):
        self._call("keepalive", interval=interval)

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=5)
        finally:
            process.stdout.close()
