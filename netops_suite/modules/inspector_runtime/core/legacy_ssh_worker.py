"""Standalone pipe worker; executed exclusively by the pinned legacy Python."""
import base64
import hashlib
import hmac
import json
import sys

import paramiko


class NoneAuthStrategy:
    """Preserve devices which authenticate in the interactive shell."""

    def __init__(self, username):
        self.username = username

    def authenticate(self, transport):
        transport.auth_none(self.username)


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint

    def missing_host_key(self, client, hostname, key):
        if not self.fingerprint:
            return
        actual = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if not hmac.compare_digest(actual, self.fingerprint):
            raise paramiko.BadHostKeyException(hostname, key, key)


def serve():
    client = paramiko.SSHClient()
    channel = None
    try:
        if paramiko.__version__ != "3.5.1":
            print(json.dumps({"error": "레거시 SSH는 고정된 Paramiko 3.5.1 런타임이 필요합니다."}), flush=True)
            return
        for line in sys.stdin:
            try:
                request = json.loads(line)
                operation = request.pop("op")
                result = None
                if operation == "connect":
                    client.set_missing_host_key_policy(PinnedHostKey(request.pop("fingerprint")))
                    timeout = request.get("timeout", 30)
                    if request.pop("auth_mode", "password") == "none":
                        request["auth_strategy"] = NoneAuthStrategy(request["username"])
                    request.setdefault("banner_timeout", timeout)
                    request.setdefault("auth_timeout", timeout)
                    client.connect(**request)
                    result = client.get_transport().get_remote_server_key().get_name()
                elif operation == "shell":
                    channel = client.invoke_shell(**request)
                elif operation == "timeout":
                    channel.settimeout(request["value"])
                elif operation == "ready":
                    if channel.closed:
                        raise EOFError("SSH channel closed")
                    result = channel.recv_ready()
                elif operation == "recv":
                    result = base64.b64encode(channel.recv(min(int(request["size"]), 65536))).decode()
                elif operation == "send":
                    data = base64.b64decode(request["value"])
                    channel.sendall(data)
                    result = len(data)
                elif operation == "active":
                    result = client.get_transport().is_active()
                elif operation == "keepalive":
                    client.get_transport().set_keepalive(request["interval"])
                else:
                    raise ValueError("Unsupported operation")
                print(json.dumps({"result": result}), flush=True)
            except Exception as exc:
                # Never echo exception messages containing connection arguments or credentials.
                errors = {
                    "BadHostKeyException": "SSH host key 지문이 일치하지 않아 인증 전에 중단했습니다.",
                    "AuthenticationException": "레거시 SSH 인증 실패: 계정 정보를 확인하세요.",
                    "IncompatiblePeer": "레거시 SSH에서도 공통 알고리즘이 없습니다. 서버 협상 목록을 확인하세요.",
                }
                message = errors.get(type(exc).__name__, "레거시 SSH 실패: " + type(exc).__name__)
                print(json.dumps({"error": message}), flush=True)
                break
    finally:
        if channel is not None:
            channel.close()
        client.close()


if __name__ == "__main__":
    serve()
