from __future__ import annotations

import socket
import threading

import pandas as pd
import pytest

from netops_suite.modules.inspector import InspectorService

with InspectorService()._runtime_import_path():
    from core import telnet_compat
    from core.validator import validate_dataframe
    from vendors.axgate import AxgateHandler
    from vendors.base import telnet_login


class FakeTelnetDevice:
    """Plain TCP CLI: optional login prompts, then a prompt that echoes commands."""

    def __init__(self, script: list[tuple[bytes, bytes | None]], prompt: bytes = b"AXGATE# "):
        self.script = script
        self.prompt = prompt
        self.received: list[bytes] = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _readline(self, conn) -> bytes:
        line = b""
        while not line.endswith(b"\n"):
            chunk = conn.recv(1)
            if not chunk:
                raise EOFError
            line += chunk
        return line.strip()

    def _serve(self) -> None:
        conn, _ = self.listener.accept()
        conn.settimeout(10)
        try:
            for send, expected in self.script:
                conn.sendall(send)
                if expected is not None:
                    line = self._readline(conn)
                    self.received.append(line)
                    if line != expected:
                        conn.sendall(b"\r\n% Login invalid\r\n")
                        return
            conn.sendall(b"\r\n" + self.prompt)
            while True:
                command = self._readline(conn)
                self.received.append(command)
                if command == b"exit":
                    return
                conn.sendall(command + b"\r\nOUTPUT OF " + command + b"\r\n" + self.prompt)
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
            self.listener.close()

    def connect(self):
        return telnet_compat.Telnet("127.0.0.1", port=self.port, timeout=5)


def test_no_password_device_opens_straight_to_cli():
    device = FakeTelnetDevice([(b"Welcome to AXGATE\r\n", None)])
    tn = device.connect()
    try:
        output = telnet_login(tn, "admin", "", timeout=3)
    finally:
        tn.close()
    assert output.rstrip().endswith("AXGATE#")
    assert device.received == []


def test_username_and_password_prompts_are_answered():
    device = FakeTelnetDevice([(b"Username: ", b"admin"), (b"Password: ", b"secret")])
    tn = device.connect()
    try:
        telnet_login(tn, "admin", "secret", timeout=3)
    finally:
        tn.close()
    assert device.received == [b"admin", b"secret"]


def test_password_only_prompt_skips_username():
    device = FakeTelnetDevice([(b"Password: ", b"secret")])
    tn = device.connect()
    try:
        telnet_login(tn, "admin", "secret", timeout=3)
    finally:
        tn.close()
    assert device.received == [b"secret"]


def test_rejected_login_is_reported_instead_of_typing_credentials_as_commands():
    device = FakeTelnetDevice([
        # Login rejected: the device asks for the username again.
        (b"Username: ", b"admin"), (b"Password: ", b"wrong"),
        (b"\r\nUsername: ", b"admin"),
    ])
    tn = device.connect()
    try:
        with pytest.raises(ConnectionError, match="계정"):
            telnet_login(tn, "admin", "wrong", timeout=3)
    finally:
        tn.close()


def test_axgate_telnet_without_password_runs_commands():
    device = FakeTelnetDevice([(b"\r\n", None)])
    handler = AxgateHandler(
        {"ip": "127.0.0.1", "port": device.port, "connection_type": "telnet",
         "vendor": "axgate", "os": "axgate", "username": "", "password": ""},
        timeout=5,
    )
    assert handler.connect()
    try:
        output = handler.send_command("show system version", timeout=0.5)
    finally:
        handler.disconnect()
    assert "OUTPUT OF show system version" in output


def test_blank_and_numeric_passwords_are_kept_as_text():
    frame = pd.DataFrame([
        {"ip": "192.0.2.10", "vendor": "axgate", "os": "axgate",
         "connection_type": "telnet", "port": 23, "password": None},
        {"ip": "192.0.2.11", "vendor": "axgate", "os": "axgate",
         "connection_type": "telnet", "port": 23, "password": 1234},
    ])
    devices = validate_dataframe(frame).to_dict("records")
    assert [device["password"] for device in devices] == ["", "1234"]
