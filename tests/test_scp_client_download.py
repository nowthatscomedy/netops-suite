from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.services.scp_client_service import ScpClientService
from app.utils.file_utils import build_app_paths


class FakeChannel:
    """Replays what a remote `scp -r -f` sends; the client's acknowledgements are recorded."""

    def __init__(self, script: bytes) -> None:
        self.script = bytearray(script)
        self.sent = bytearray()
        self.command = ""
        self.closed = False

    def settimeout(self, _value) -> None:
        pass

    def exec_command(self, command: str) -> None:
        self.command = command

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        chunk = bytes(self.script[:size])
        del self.script[:size]
        return chunk

    def exit_status_ready(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, scripts: dict[str, bytes]) -> None:
        self.scripts = scripts
        self.channels: list[FakeChannel] = []
        self._pending = list(scripts.values())

    def open_session(self) -> FakeChannel:
        channel = FakeChannel(self._pending.pop(0))
        self.channels.append(channel)
        return channel

    def get_remote_server_key(self):
        return None


class FakeClient:
    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport

    def get_transport(self) -> FakeTransport:
        return self.transport

    def close(self) -> None:
        pass


def file_record(name: str, data: bytes) -> bytes:
    return f"C0644 {len(data)} {name}\n".encode() + data + b"\x00"


def build_service(tmp_path: Path) -> ScpClientService:
    return ScpClientService(build_app_paths(tmp_path / "app"), logging.getLogger("scp-download-test"))


def run_download(tmp_path: Path, monkeypatch, scripts: dict[str, bytes]):
    service = build_service(tmp_path)
    transport = FakeTransport(scripts)
    monkeypatch.setattr(service, "_open_ssh_client", lambda *args: FakeClient(transport))
    local = tmp_path / "local"
    local.mkdir()
    result = service.download_files("10.0.0.1", 2222, "root", "pw", list(scripts), str(local), 5)
    return result, transport, local


def test_download_uses_legacy_scp_command_with_custom_port_paths(tmp_path, monkeypatch):
    data = b"rotate-archive" * 100
    result, transport, local = run_download(
        tmp_path, monkeypatch, {"/secui/tmp/rotate.tar.gz": file_record("rotate.tar.gz", data)}
    )

    assert transport.channels[0].command == "scp -r -f /secui/tmp/rotate.tar.gz"
    assert (local / "rotate.tar.gz").read_bytes() == data
    assert result.success
    [item] = result.payload["results"]
    assert (item.status, item.source_path) == ("완료", "/secui/tmp/rotate.tar.gz")


def test_download_receives_wildcards_and_folders_and_continues_after_missing_path(tmp_path, monkeypatch):
    folder = b"D0755 0 rotate\n" + file_record("a.log", b"aa") + b"D0755 0 sub\n" + file_record("b.log", b"b") + b"E\nE\n"
    result, transport, local = run_download(
        tmp_path,
        monkeypatch,
        {
            "/var/rotate/*.tar.gz": file_record("one.tar.gz", b"1") + file_record("two.tar.gz", b"22"),
            "/var/missing.tar.gz": b"\x01scp: /var/missing.tar.gz: No such file or directory\n",
            "/var/rotate": folder,
        },
    )

    assert transport.channels[0].command == "scp -r -f /var/rotate/*.tar.gz"
    assert (local / "one.tar.gz").read_bytes() == b"1"
    assert (local / "two.tar.gz").read_bytes() == b"22"
    assert (local / "rotate" / "a.log").read_bytes() == b"aa"
    assert (local / "rotate" / "sub" / "b.log").read_bytes() == b"b"
    statuses = [(item.status, item.source_path) for item in result.payload["results"]]
    assert ("완료", "/var/rotate/two.tar.gz") in statuses
    assert ("완료", "/var/rotate/sub/b.log") in statuses
    assert ("오류", "/var/missing.tar.gz") in statuses
    assert result.success
    assert "4개 파일" in result.message and "1건은 받지 못했습니다" in result.message


def test_remote_path_is_quoted_except_wildcards(tmp_path):
    service = build_service(tmp_path)
    assert service._quote_remote_source("/tmp/my logs/*.gz") == "'/tmp/my logs/'*.gz"
    assert service._quote_remote_source("/tmp/a;rm -rf x") == "'/tmp/a;rm -rf x'"


@pytest.mark.parametrize("name", ["../evil", "..", "dir/evil", "c:evil"])
def test_unsafe_remote_names_are_rejected(tmp_path, monkeypatch, name):
    result, _transport, local = run_download(tmp_path, monkeypatch, {"/x": file_record(name, b"x")})

    assert not result.success
    [item] = result.payload["results"]
    assert item.status == "오류" and "안전하지 않은 파일 이름" in item.error
    assert not (tmp_path / "evil").exists()
    assert list(local.iterdir()) == []


def test_truncated_file_is_removed_and_reported(tmp_path, monkeypatch):
    result, _transport, local = run_download(tmp_path, monkeypatch, {"/big.bin": b"C0644 10 big.bin\nabc"})

    [item] = result.payload["results"]
    assert item.status == "오류" and "연결이 종료" in item.error
    assert not (local / "big.bin").exists()


def test_connection_timeout_points_at_the_ssh_port(tmp_path, monkeypatch):
    service = build_service(tmp_path)

    class TimeoutClient:
        def set_missing_host_key_policy(self, _policy) -> None:
            pass

        def connect(self, **_kwargs) -> None:
            raise TimeoutError("timed out")

        def close(self) -> None:
            pass

    import app.services.scp_client_service as module

    monkeypatch.setattr(module.paramiko, "SSHClient", TimeoutClient)
    with pytest.raises(RuntimeError) as error:
        service.download_files("203.0.113.5", 22, "root", "pw", ["/x"], str(tmp_path), 3)
    message = str(error.value)
    assert "203.0.113.5:22" in message and "scp -P 2222" in message and "포트" in message
