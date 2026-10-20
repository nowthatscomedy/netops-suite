from __future__ import annotations

import hashlib
import logging
import posixpath
import re
import shlex
import socket
import time
from pathlib import Path

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


class _ReportedScpFailure(RuntimeError):
    """A download failure already shown as its own row in the results."""


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
                    received: list[ScpTransferResult] = []
                    try:
                        channel = transport.open_session()
                        channel.settimeout(validated_timeout)
                        # Same remote command as OpenSSH `scp -O -r`, so no SFTP subsystem is needed.
                        channel.exec_command(f"scp -r -f {self._quote_remote_source(remote_source)}")
                        try:
                            self._receive_sources(
                                channel=channel,
                                remote_source=remote_source,
                                local_root=local_root,
                                results=received,
                                progress_callback=progress_callback,
                                cancel_event=cancel_event,
                            )
                        finally:
                            self._close_channel(channel)
                    except ScpTransferCancelled:
                        results.extend(received)
                        raise
                    except _ReportedScpFailure:
                        pass
                    except Exception as exc:  # noqa: BLE001 - one failed path must not stop the rest
                        received.append(self._failed_download(remote_source, local_root, exc, progress_callback))
                    results.extend(received)
            except ScpTransferCancelled:
                return OperationResult(
                    True,
                    "SCP 다운로드를 중지했습니다.",
                    payload={"results": results, "host_key_fingerprint": fingerprint},
                )

            completed = sum(1 for item in results if item.status == "완료")
            failed = sum(1 for item in results if item.status == "오류")
            message = f"{completed}개 파일 다운로드를 마쳤습니다."
            if failed:
                message += f" {failed}건은 받지 못했습니다. 결과 표의 상태와 로그를 확인하세요."
            return OperationResult(
                completed > 0 or not failed,
                message,
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
        try:
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
        except Exception as exc:
            client.close()
            raise RuntimeError(self._connection_failure_message(host, port, exc)) from exc
        return client

    def _connection_failure_message(self, host: str, port: int, exc: Exception) -> str:
        endpoint = f"{host}:{port}"
        port_hint = (
            "장비의 SSH 포트가 맞는지 확인하세요. 명령줄에서 scp -P 2222처럼 포트를 지정했다면 "
            "포트 칸에도 같은 번호를 입력합니다."
        )
        if paramiko is not None and isinstance(exc, paramiko.AuthenticationException):
            return f"{endpoint} 로그인에 실패했습니다. 사용자명과 비밀번호를 확인하세요."
        refused = isinstance(exc, ConnectionRefusedError) or (
            paramiko is not None and isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError)
        )
        if refused:
            return f"{endpoint}에서 연결을 거부했습니다. {port_hint}"
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return (
                f"{endpoint}에서 제한 시간 안에 응답이 없습니다. {port_hint} "
                "중간 방화벽이 이 PC에서 장비로 나가는 그 포트를 허용하는지도 확인하세요."
            )
        if paramiko is not None and isinstance(exc, paramiko.SSHException):
            return f"{endpoint}와 SSH 연결을 맺지 못했습니다: {exc}"
        return f"{endpoint}에 연결하지 못했습니다: {exc or exc.__class__.__name__}"

    def _quote_remote_source(self, value: str) -> str:
        """Quote a download path but leave `*` and `?` to the remote shell, as `scp -O` does."""
        parts = re.split(r"([*?]+)", self._normalize_remote_path(value))
        return "".join(part if not part or part[0] in "*?" else shlex.quote(part) for part in parts)

    def _receive_sources(self, channel, remote_source: str, local_root: Path, results: list, progress_callback, cancel_event) -> None:
        remote_base = posixpath.dirname(remote_source.rstrip("/"))
        directories: list[str] = []
        remote_errors = 0
        channel.sendall(b"\x00")
        while True:
            code = channel.recv(1)
            if not code:
                break
            value = code[0]
            if value in {1, 2}:
                # 1 is a per-file warning (for example a missing path); 2 ends the transfer.
                message = self._read_line(channel) or "SCP 원격 오류"
                remote_errors += 1
                results.append(self._failed_download(remote_source, local_root, RuntimeError(message), progress_callback))
                if value == 2:
                    break
                continue
            control = chr(value)
            payload = self._read_line(channel)
            if control == "T":
                channel.sendall(b"\x00")
            elif control == "D":
                directories.append(self._safe_remote_name(payload.split(" ", 2)[-1]))
                local_root.joinpath(*directories).mkdir(parents=True, exist_ok=True)
                channel.sendall(b"\x00")
            elif control == "E":
                if directories:
                    directories.pop()
                channel.sendall(b"\x00")
            elif control == "C":
                mode, size_text, filename = (payload.split(" ", 2) + ["", ""])[:3]
                if not mode.isdigit() or not size_text.isdigit():
                    raise RuntimeError("SCP 파일 헤더를 해석하지 못했습니다.")
                name = self._safe_remote_name(filename)
                self._receive_file(
                    channel=channel,
                    remote_source=posixpath.join(remote_base, *directories, name),
                    local_path=local_root.joinpath(*directories, name),
                    size=int(size_text),
                    results=results,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
            else:
                raise RuntimeError("SCP 서버가 파일 전송 헤더를 보내지 않았습니다.")
        if not results and not remote_errors:
            raise RuntimeError("원격 장비가 보낸 파일이 없습니다. 경로를 확인하세요.")

    def _download_error_text(self, exc: Exception) -> str:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return "원격 장비 응답 시간이 초과되었습니다. 경로를 확인하거나 타임아웃(초)을 늘려 보세요."
        return str(exc) or exc.__class__.__name__

    def _safe_remote_name(self, name: str) -> str:
        text = str(name or "")
        if not text or text in {".", ".."} or any(char in text for char in '/\\:\0'):
            raise RuntimeError(f"원격 장비가 안전하지 않은 파일 이름을 보냈습니다: {text!r}")
        return text

    def _failed_download(self, remote_source: str, local_root: Path, exc: Exception, progress_callback) -> ScpTransferResult:
        message = self._download_error_text(exc)
        result = ScpTransferResult(
            action="다운로드",
            source_path=remote_source,
            target_path=str(local_root),
            status="오류",
            error=message,
        )
        self._emit_transfer(progress_callback, result)
        self._emit_log(progress_callback, f"[SCP 다운로드 실패] {remote_source}: {message}")
        return result

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

    def _receive_file(self, channel, remote_source: str, local_path: Path, size: int, results: list, progress_callback, cancel_event) -> None:
        result = ScpTransferResult(
            action="다운로드",
            source_path=remote_source,
            target_path=str(local_path),
            size_bytes=size,
            status="진행 중",
        )
        results.append(result)
        self._emit_log(progress_callback, f"[SCP 다운로드 시작] {remote_source} -> {local_path}")
        started = time.perf_counter()
        transferred = 0
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            channel.sendall(b"\x00")
            with local_path.open("wb") as handle:
                while transferred < size:
                    if cancel_event is not None and cancel_event.is_set():
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
        except ScpTransferCancelled:
            local_path.unlink(missing_ok=True)
            result.status = "중지"
            self._emit_transfer(progress_callback, result)
            raise
        except Exception as exc:
            local_path.unlink(missing_ok=True)
            result.status = "오류"
            result.error = self._download_error_text(exc)
            self._emit_transfer(progress_callback, result)
            self._emit_log(progress_callback, f"[SCP 다운로드 실패] {remote_source}: {result.error}")
            raise _ReportedScpFailure(result.error) from exc
        result.transferred_bytes = size
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        self._emit_log(progress_callback, f"[SCP 다운로드 완료] {local_path}")

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
