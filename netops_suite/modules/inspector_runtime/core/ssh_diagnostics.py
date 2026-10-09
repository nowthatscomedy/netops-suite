"""SSH reachability probe and plain-language hints for connection failures.

The probe reads only the server's version banner and its KEXINIT algorithm
lists, then disconnects. It never authenticates and sends no commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import socket
import struct

# Algorithms only the isolated legacy runtime (Paramiko 3.5.1) still accepts.
LEGACY_ONLY_ALGORITHMS = {
    "kex": (
        "diffie-hellman-group-exchange-sha1",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
    ),
    "host_keys": ("ssh-rsa", "ssh-dss"),
    "ciphers": (),
    "macs": (),
}
_GROUP_LABELS = {
    "kex": "키 교환",
    "host_keys": "호스트 키",
    "ciphers": "암호화",
    "macs": "무결성(MAC)",
}
_MSG_KEXINIT = 20
_MAX_PACKET = 256 * 1024


class SSHProbeError(RuntimeError):
    """The endpoint could not be read as an SSH server."""


@dataclass(slots=True)
class SSHProbeResult:
    banner: str
    algorithms: dict[str, list[str]]
    modern_missing: list[str] = field(default_factory=list)
    legacy_missing: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        if not self.modern_missing:
            return "modern"
        if not self.legacy_missing:
            return "legacy"
        return "unsupported"

    def summary(self) -> str:
        if self.mode == "modern":
            return "기본 SSH로 접속할 수 있는 장비입니다."
        if self.mode == "legacy":
            reasons = ", ".join(_GROUP_LABELS[name] for name in self.modern_missing)
            return (
                f"오래된 SSH 방식({reasons})만 지원하는 장비입니다. "
                "구형 장비 호환 모드로 자동 전환해 접속합니다."
            )
        reasons = ", ".join(_GROUP_LABELS[name] for name in self.legacy_missing)
        return (
            f"장비가 제공하는 {reasons} 방식을 이 프로그램이 지원하지 않습니다. "
            "장비에서 SSH 설정을 확인하거나 Telnet 접속을 사용하세요."
        )

    def details(self) -> str:
        lines = [f"SSH 버전 정보: {self.banner}"]
        for name, label in _GROUP_LABELS.items():
            offered = ", ".join(self.algorithms.get(name, [])) or "(없음)"
            lines.append(f"{label}: {offered}")
        return "\n".join(lines)


def modern_algorithms() -> dict[str, tuple[str, ...]]:
    import paramiko

    transport = paramiko.Transport
    return {
        "kex": tuple(transport._preferred_kex),
        "host_keys": tuple(transport._preferred_keys),
        "ciphers": tuple(transport._preferred_ciphers),
        "macs": tuple(transport._preferred_macs),
    }


def evaluate(
    banner: str,
    algorithms: dict[str, list[str]],
    modern: dict[str, tuple[str, ...]] | None = None,
) -> SSHProbeResult:
    modern = modern or modern_algorithms()
    modern_missing: list[str] = []
    legacy_missing: list[str] = []
    for name in _GROUP_LABELS:
        offered = set(algorithms.get(name, []))
        supported = set(modern.get(name, ()))
        if not offered & supported:
            modern_missing.append(name)
        if not offered & (supported | set(LEGACY_ONLY_ALGORITHMS[name])):
            legacy_missing.append(name)
    return SSHProbeResult(banner, algorithms, modern_missing, legacy_missing)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise SSHProbeError("장비가 SSH 협상 도중 연결을 끊었습니다.")
        data += chunk
    return data


def _read_banner(sock: socket.socket) -> str:
    # RFC 4253 allows text lines before the identification string.
    for _ in range(20):
        line = b""
        while not line.endswith(b"\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise SSHProbeError("장비가 SSH 버전 정보를 보내기 전에 연결을 끊었습니다.")
            line += chunk
            if len(line) > 1024:
                raise SSHProbeError("SSH 버전 정보가 너무 깁니다.")
        text = line.decode("utf-8", "replace").strip()
        if text.startswith("SSH-"):
            return text
    raise SSHProbeError("SSH 서버 응답이 아닙니다. 포트 번호를 확인하세요.")


def parse_kexinit(payload: bytes) -> dict[str, list[str]]:
    if not payload or payload[0] != _MSG_KEXINIT:
        raise SSHProbeError("SSH 알고리즘 목록을 받지 못했습니다.")
    offset = 1 + 16  # message id + cookie
    lists: list[list[str]] = []
    for _ in range(6):
        if offset + 4 > len(payload):
            raise SSHProbeError("SSH 알고리즘 목록이 잘렸습니다.")
        (length,) = struct.unpack(">I", payload[offset:offset + 4])
        offset += 4
        raw = payload[offset:offset + length].decode("ascii", "replace")
        offset += length
        lists.append([item for item in raw.split(",") if item])
    kex, host_keys, ciphers_c2s, _ciphers_s2c, macs_c2s, _macs_s2c = lists
    return {"kex": kex, "host_keys": host_keys, "ciphers": ciphers_c2s, "macs": macs_c2s}


def probe_ssh(host: str, port: int = 22, timeout: float = 5.0) -> SSHProbeResult:
    """Read what the server offers without logging in."""
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as exc:
        raise SSHProbeError(f"{host}:{port}에 TCP로 연결하지 못했습니다. ({exc})") from exc
    try:
        sock.settimeout(timeout)
        try:
            banner = _read_banner(sock)
            sock.sendall(b"SSH-2.0-NetOpsSuite_Probe\r\n")
            (packet_length,) = struct.unpack(">I", _read_exact(sock, 4))
            if not 5 <= packet_length <= _MAX_PACKET:
                raise SSHProbeError("SSH 알고리즘 목록의 크기가 올바르지 않습니다.")
            packet = _read_exact(sock, packet_length)
        except socket.timeout as exc:
            raise SSHProbeError(
                "장비가 제한 시간 안에 SSH 응답을 보내지 않았습니다."
            ) from exc
        padding = packet[0]
        payload = packet[1:packet_length - padding]
        return evaluate(banner, parse_kexinit(payload))
    finally:
        sock.close()


_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("구형 장비용 SSH 구성 요소", "레거시 SSH 런타임이 없습니다"),
        "구형 장비 호환 구성 요소가 없습니다. NetOps Suite를 다시 설치하세요.",
    ),
    (
        ("tcp 연결 테스트 실패",),
        "IP와 포트가 맞는지, 장비에서 SSH/Telnet이 켜져 있는지, 방화벽이 막지 않는지 확인하세요.",
    ),
    (
        ("error reading ssh protocol banner", "banner"),
        "장비가 SSH 응답을 늦게 보내거나 동시 접속 수(VTY)가 가득 찼습니다. "
        "잠시 후 다시 시도하거나 동시 실행 수를 줄이세요.",
    ),
    (
        ("지문",),
        "장비의 SSH 호스트 키가 장비 목록의 ssh_host_key_sha256 값과 다릅니다. "
        "장비가 교체됐는지 확인한 뒤 값을 고치세요.",
    ),
    (
        ("no acceptable", "공통 알고리즘", "incompatible ssh peer", "호환성 오류"),
        "장비가 지원하는 SSH 방식이 맞지 않습니다. 프로파일 만들기의 '장비로 시험'에서 "
        "SSH 진단을 실행하면 장비가 제공하는 방식을 볼 수 있습니다.",
    ),
    (
        ("authentication", "인증 실패", "auth failed"),
        "계정 또는 비밀번호를 확인하세요. 장비에 따라 로그인 후 enable 비밀번호가 따로 필요합니다.",
    ),
    (
        ("pattern not detected", "프롬프트", "prompt"),
        "로그인 후 장비 프롬프트를 찾지 못했습니다. 프로파일의 전문가 설정에서 "
        "접속 장비 유형(device_type)을 확인하세요.",
    ),
    (
        ("timed out", "timeout", "시간 초과"),
        "장비 응답이 느립니다. 실행 옵션에서 시간 제한을 늘려 다시 시도하세요.",
    ),
)


def action_hint(message: str) -> str:
    """Return a one-line Korean next step for a connection error, or ''."""
    text = str(message or "").lower()
    for needles, hint in _HINTS:
        if any(needle.lower() in text for needle in needles):
            return hint
    return ""


def with_action_hint(message: str) -> str:
    hint = action_hint(message)
    if not hint or hint in message:
        return message
    return f"{message} / 조치: {hint}"
