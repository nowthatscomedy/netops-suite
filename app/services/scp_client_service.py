from __future__ import annotations

import hashlib
import logging
import posixpath
import shlex
import time
from pathlib import Path, PurePosixPath

try:
    import paramiko
except ImportError:  # pragma: no cover - optional dependency guard
    paramiko = None  # type: ignore[assignment]

from app.models.result_models import OperationResult
from app.models.scp_models import ScpTransferResult
from app.utils.file_utils import AppPaths, execution_environment_label, is_packaged_runtime
from app.utils.validators import (
    parse_positive_int,
    require_text,
    validate_existing_directory,
    validate_ftp_host,
    validate_ftp_username,
)


class ScpTransferCancelled(RuntimeError):
    pass


class ScpClientService:
    _CHUNK_SIZE = 64 * 1024

    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger

    def runtime_support_status(self) -> OperationResult:
        environment = execution_environment_label()
        if paramiko is None:
            return OperationResult(
                False,
                (
                    f"{environment}: SCP 클라이언트를 사용하려면 requirements.txt 설치가 필요합니다."
                    if not is_packaged_runtime()
                    else f"{environment}: SCP 클라이언트 구성요소가 누락되었거나 손상되었을 수 있습니다."
                ),
                details=self._dependency_resolution_message("SCP 클라이언트", "paramiko"),
            )
        return OperationResult(
            True,
            f"{environment}: SCP 클라이언트 사용 준비가 완료되었습니다.",
            details="필수 런타임 의존성이 확인되었습니다.",
        )

    def upload_files(
        self,
        host: str,
        port: int | str | None,
        username: str,
        password: str,
        local_paths: list[str],
        remote_path: str,
        timeout_seconds: int | str = 15,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        self._ensure_paramiko_support()
        validated_host = validate_ftp_host(host)
        validated_port = self._validate_port(port or 22)
        validated_username = validate_ftp_username(username, "sftp")
        validated_password = require_text(password, "비밀번호")
        validated_timeout = parse_positive_int(timeout_seconds, "SCP 타임아웃", minimum=1, maximum=300)
        source_paths = [Path(item).expanduser() for item in local_paths if str(item).strip()]
        if not source_paths:
            raise ValueError("업로드할 파일을 선택해 주세요.")
        for local_path in source_paths:
            if not local_path.exists() or not local_path.is_file():
                raise ValueError(f"업로드 대상 파일이 없습니다: {local_path}")

        remote_target = self._normalize_remote_path(remote_path)
        use_directory_target = len(source_paths) > 1 or remote_target.endswith("/") or remote_target in {"", ".", "./"}
        results: list[ScpTransferResult] = []

        client = self._open_ssh_client(validated_host, validated_port, validated_username, validated_password, validated_timeout)
        try:
            transport = client.get_transport()
            fingerprint = self._fingerprint_host_key(transport)
            self._emit_log(
                progress_callback,
                f"[SCP 연결] {validated_host}:{validated_port} / 사용자 {validated_username} / 서버 지문 {fingerprint or '-'}",
            )

            channel = transport.open_session()
            channel.settimeout(validated_timeout)
            command_target = remote_target or "."
            command = f"scp -t {'-d ' if use_directory_target else ''}{self._quote_remote(command_target)}"
            channel.exec_command(command)
            self._expect_ok(channel)

            try:
                for local_path in source_paths:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ScpTransferCancelled()

                    if use_directory_target:
                        result_target = self._join_remote(command_target, local_path.name)
                    else:
                        result_target = command_target
                    result = self._send_file(
                        channel=channel,
                        local_path=local_path,
                        remote_path=result_target,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                    results.append(result)
            except ScpTransferCancelled:
                channel.close()
                return OperationResult(
                    True,
                    "SCP 업로드를 중지했습니다.",
                    payload={"results": results, "host_key_fingerprint": fingerprint},
                )
            self._close_channel(channel)
            return OperationResult(
                True,
                f"{sum(1 for item in results if item.status == '완료')}개 파일 업로드를 마쳤습니다.",
                payload={"results": results, "host_key_fingerprint": fingerprint},
            )
        finally:
            client.close()

    def download_files(
        self,
        host: str,
        port: int | str | None,
        username: str,
        password: str,
        remote_sources: list[str],
        local_dir: str,
        timeout_seconds: int | str = 15,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        self._ensure_paramiko_support()
        validated_host = validate_ftp_host(host)
        validated_port = self._validate_port(port or 22)
        validated_username = validate_ftp_username(username, "sftp")
        validated_password = require_text(password, "비밀번호")
        validated_timeout = parse_positive_int(timeout_seconds, "SCP 타임아웃", minimum=1, maximum=300)
        local_root = Path(validate_existing_directory(local_dir, "로컬 폴더"))
        normalized_sources = [self._normalize_remote_path(item) for item in remote_sources if str(item).strip()]
        if not normalized_sources:
            raise ValueError("다운로드할 원격 경로를 한 줄에 하나씩 입력해 주세요.")

        results: list[ScpTransferResult] = []
        client = self._open_ssh_client(validated_host, validated_port, validated_username, validated_password, validated_timeout)
        try:
            transport = client.get_transport()
            fingerprint = self._fingerprint_host_key(transport)
            self._emit_log(
                progress_callback,
                f"[SCP 연결] {validated_host}:{validated_port} / 사용자 {validated_username} / 서버 지문 {fingerprint or '-'}",
            )
            try:
                for remote_source in normalized_sources:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ScpTransferCancelled()
                    channel = transport.open_session()
                    channel.settimeout(validated_timeout)
                    channel.exec_command(f"scp -f {self._quote_remote(remote_source)}")
                    result = self._receive_file(
                        channel=channel,
                        remote_source=remote_source,
                        local_root=local_root,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                    results.append(result)
                    self._close_channel(channel)
            except ScpTransferCancelled:
                return OperationResult(
                    True,
                    "SCP 다운로드를 중지했습니다.",
                    payload={"results": results, "host_key_fingerprint": fingerprint},
                )

            return OperationResult(
                True,
                f"{sum(1 for item in results if item.status == '완료')}개 파일 다운로드를 마쳤습니다.",
                payload={"results": results, "host_key_fingerprint": fingerprint},
            )
        finally:
            client.close()

    def _open_ssh_client(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout_seconds: int,
    ):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout_seconds,
            banner_timeout=timeout_seconds,
            auth_timeout=timeout_seconds,
            look_for_keys=False,
            allow_agent=False,
        )
        return client

    def _send_file(self, channel, local_path: Path, remote_path: str, progress_callback, cancel_event) -> ScpTransferResult:
        size = local_path.stat().st_size
        result = ScpTransferResult(
            action="업로드",
            source_path=str(local_path),
            target_path=remote_path,
            size_bytes=size,
            status="진행 중",
        )
        self._emit_log(progress_callback, f"[SCP 업로드 시작] {local_path} -> {remote_path}")

        header = f"C0644 {size} {local_path.name}\n".encode("utf-8")
        channel.sendall(header)
        self._expect_ok(channel)

        started = time.perf_counter()
        transferred = 0
        with local_path.open("rb") as handle:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise ScpTransferCancelled()
                chunk = handle.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                channel.sendall(chunk)
                transferred += len(chunk)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)

        channel.sendall(b"\x00")
        self._expect_ok(channel)
        result.transferred_bytes = size
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        self._emit_log(progress_callback, f"[SCP 업로드 완료] {remote_path}")
        return result

    def _receive_file(self, channel, remote_source: str, local_root: Path, progress_callback, cancel_event) -> ScpTransferResult:
        channel.sendall(b"\x00")
        control, payload = self._read_command(channel)
        while control == "T":
            channel.sendall(b"\x00")
            control, payload = self._read_command(channel)
        if control != "C":
            raise RuntimeError("SCP 서버가 파일 전송 헤더를 보내지 않았습니다.")

        mode, size_text, filename = payload.split(" ", 2)
        if control == "C" and not mode.isdigit():
            raise RuntimeError("SCP 파일 헤더를 해석하지 못했습니다.")
        size = int(size_text)
        local_path = local_root / filename
        result = ScpTransferResult(
            action="다운로드",
            source_path=remote_source,
            target_path=str(local_path),
            size_bytes=size,
            status="진행 중",
        )

        self._emit_log(progress_callback, f"[SCP 다운로드 시작] {remote_source} -> {local_path}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        channel.sendall(b"\x00")
        started = time.perf_counter()
        transferred = 0
        with local_path.open("wb") as handle:
            while transferred < size:
                if cancel_event is not None and cancel_event.is_set():
                    handle.close()
                    local_path.unlink(missing_ok=True)
                    raise ScpTransferCancelled()
                chunk = channel.recv(min(self._CHUNK_SIZE, size - transferred))
                if not chunk:
                    raise RuntimeError("SCP 다운로드 중 연결이 종료되었습니다.")
                handle.write(chunk)
                transferred += len(chunk)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)

        self._expect_ok(channel)
        channel.sendall(b"\x00")
        result.transferred_bytes = size
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        self._emit_log(progress_callback, f"[SCP 다운로드 완료] {local_path}")
        return result

    def _read_command(self, channel) -> tuple[str, str]:
        code = channel.recv(1)
        if not code:
            raise RuntimeError("SCP 채널 응답이 없습니다.")
        value = code[0]
        if value in {1, 2}:
            raise RuntimeError(self._read_line(channel) or "SCP 원격 오류")
        control = chr(value)
        return control, self._read_line(channel)

    def _expect_ok(self, channel) -> None:
        code = channel.recv(1)
        if not code:
            raise RuntimeError("SCP 채널 응답이 없습니다.")
        value = code[0]
        if value == 0:
            return
        if value in {1, 2}:
            raise RuntimeError(self._read_line(channel) or "SCP 원격 오류")
        raise RuntimeError(f"SCP 응답 코드 {value}를 처리할 수 없습니다.")

    def _read_line(self, channel) -> str:
        buffer = bytearray()
        while True:
            chunk = channel.recv(1)
            if not chunk:
                break
            if chunk == b"\n":
                break
            buffer.extend(chunk)
        return buffer.decode("utf-8", errors="replace").strip()

    def _fingerprint_host_key(self, transport) -> str:
        key = transport.get_remote_server_key()
        if key is None:
            return ""
        digest = hashlib.sha256(key.asbytes()).hexdigest().upper()
        return "SHA256:" + digest

    def _validate_port(self, value: int | str) -> int:
        return parse_positive_int(value, "SCP 포트", minimum=1, maximum=65535)

    def _normalize_remote_path(self, value: str) -> str:
        text = str(value or "").strip().replace("\\", "/")
        return text or "."

    def _join_remote(self, base: str, name: str) -> str:
        normalized_base = self._normalize_remote_path(base)
        if normalized_base in {".", ""}:
            return name
        if normalized_base.endswith("/"):
            return f"{normalized_base}{name}"
        return posixpath.join(normalized_base, name)

    def _quote_remote(self, value: str) -> str:
        return shlex.quote(self._normalize_remote_path(value))

    def _emit_log(self, progress_callback, message: str) -> None:
        self.logger.info(message)
        if progress_callback is not None:
            progress_callback.emit({"kind": "log", "message": message})

    def _emit_transfer(self, progress_callback, result: ScpTransferResult) -> None:
        if progress_callback is not None:
            progress_callback.emit({"kind": "transfer", "result": result})

    def _close_channel(self, channel) -> None:
        try:
            if channel.exit_status_ready():
                _ = channel.recv_exit_status()
        except Exception:
            pass
        finally:
            channel.close()

    def _ensure_paramiko_support(self) -> None:
        if paramiko is not None:
            return
        support = self.runtime_support_status()
        raise RuntimeError("\n\n".join(part for part in (support.message, support.details) if part))

    def _dependency_resolution_message(self, feature_name: str, dependency_name: str) -> str:
        if is_packaged_runtime():
            return (
                f"{feature_name} 배포 구성요소({dependency_name})를 불러오지 못했습니다.\n"
                "설치본이 손상되었거나 배포에 누락이 있을 수 있습니다.\n"
                "프로그램을 다시 설치하거나 최신 설치본으로 업데이트해 주세요."
            )
        return (
            f"{feature_name} 개발 의존성({dependency_name})이 현재 환경에 없습니다.\n"
            "프로젝트 루트에서 `pip install -r requirements.txt`를 실행한 뒤 다시 시도해 주세요."
        )
