"""Local SSH protocol fixture; run using the isolated Paramiko 3 runtime."""
import base64
import hashlib
import json
from pathlib import Path
import socket
import sys
import threading

import paramiko


class Server(paramiko.ServerInterface):
    def __init__(self, auth_marker):
        self.auth_marker = auth_marker
        self.shell = threading.Event()

    def check_auth_password(self, username, password):
        self.auth_marker.write_text("authentication attempted")
        return paramiko.AUTH_SUCCESSFUL if password == "test-password" else paramiko.AUTH_FAILED

    def check_auth_none(self, username):
        if len(sys.argv) > 4 and sys.argv[4] == "none":
            self.auth_marker.write_text("none")
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_shell_request(self, channel):
        self.shell.set()
        return True


def main():
    key = paramiko.DSSKey.generate() if sys.argv[1] == "dss" else paramiko.RSAKey.generate(2048)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(20)
        print(json.dumps({"port": listener.getsockname()[1], "fingerprint": fingerprint}), flush=True)
        for _ in range(int(sys.argv[3]) if len(sys.argv) > 3 else 1):
            connection, _ = listener.accept()
            transport = paramiko.Transport(connection)
            transport.add_server_key(key)
            if sys.argv[1] == "both":
                transport.add_server_key(paramiko.DSSKey.generate())
            server = Server(Path(sys.argv[2]))
            try:
                transport.start_server(server=server)
                channel = transport.accept(10)
                if channel is None or not server.shell.wait(10):
                    continue
                channel.sendall("OmniSwitch#\n")
                channel.settimeout(10)
                while True:
                    command = channel.recv(4096)
                    if not command:
                        break
                    channel.sendall(command + b"TEST OUTPUT\nOmniSwitch#\n")
            except (EOFError, OSError, paramiko.SSHException):
                pass
            finally:
                transport.close()


if __name__ == "__main__":
    main()
