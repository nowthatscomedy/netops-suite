from __future__ import annotations

import errno
import hashlib
import logging
import os
import shlex
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath

try:
    import paramiko
except ImportError:  # pragma: no cover - optional dependency guard
    paramiko = None  # type: ignore[assignment]

from app.models.result_models import OperationResult
from app.models.scp_models import ScpServerRuntime, ScpTransferResult
from app.utils.file_utils import AppPaths, execution_environment_label, is_packaged_runtime
from app.utils.validators import (
    parse_positive_int,
    require_text,
    validate_existing_directory,
    validate_ftp_username,
    validate_optional_ipv4,
)


class _ScpServerInterface(paramiko.ServerInterface if paramiko is not None else object):
    def __init__(self, username: str, password: str, on_log) -> None:
        if paramiko is not None:
            super().__init__()
        self.username = username
        self.password = password
        self.on_log = on_log
        self.command = ""
        self.command_event = threading.Event()

    def check_auth_password(self, username: str, password: str):
        if username == self.username and password == self.password:
            self.on_log(f"[SCP 로그인] {username}")
            return paramiko.AUTH_SUCCESSFUL
        self.on_log(f"[SCP 인증 실패] {username}")
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str):
        return "password"

    def check_channel_request(self, kind: str, chanid: int):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.command = command.decode("utf-8", errors="replace")
        self.command_event.set()
        return True


class ScpServerService:
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
                    f"{environment}: SCP 서버를 사용하려면 requirements.txt 설치가 필요합니다."
                    if not is_packaged_runtime()
                    else f"{environment}: SCP 서버 구성요소가 누락되었거나 손상되었을 수 있습니다."
                ),
                details=self._dependency_resolution_message("SCP 서버", "paramiko"),
            )
        return OperationResult(
            True,
            f"{environment}: SCP 서버 사용 준비가 완료되었습니다.",
            details="필수 런타임 의존성이 확인되었습니다.",
        )

    def run_temporary_server(
        self,
        bind_host: str,
        port: int | str | None,
        root_folder: str,
        username: str,
        password: str,
        read_only: bool,
        cancel_event=None,
        progress_callback=None,
    ) -> OperationResult:
        self._ensure_paramiko_support()
        actual_bind_host = validate_optional_ipv4(bind_host, "바인드 IP") or "0.0.0.0"
        validated_port = parse_positive_int(port or 2223, "SCP 서버 포트", minimum=1, maximum=65535)
        validated_root = Path(validate_existing_directory(root_folder, "공유 루트 폴더")).resolve()
        validated_username = validate_ftp_username(username, "sftp")
        validated_password = require_text(password, "비밀번호")

        runtime = ScpServerRuntime(
            bind_host=self._display_host(actual_bind_host),
            port=validated_port,
            root_folder=str(validated_root),
            username=validated_username,
            read_only=bool(read_only),
            host_key_fingerprint=self._host_key_fingerprint(self.ensure_host_key()),
        )

        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((actual_bind_host, validated_port))
            listener.listen(20)
            listener.settimeout(0.5)
        except OSError as exc:
            raise RuntimeError(self._friendly_bind_error(exc, validated_port)) from exc

        host_key = paramiko.RSAKey.from_private_key_file(str(self.ensure_host_key()))
        session_counter = {"count": 0}
        counter_lock = threading.Lock()
        worker_threads: list[threading.Thread] = []

        def emit_log(text: str) -> None:
            self.logger.info(text)
            if progress_callback is not None:
                progress_callback.emit({"kind": "server_log", "message": text})

        def emit_runtime() -> None:
            if progress_callback is not None:
                progress_callback.emit(
                    {"kind": "server_runtime", "runtime": replace(runtime, session_count=session_counter["count"])}
                )

        def handle_client(client_socket: socket.socket, client_address: tuple[str, int]) -> None:
            transport = paramiko.Transport(client_socket)
            transport.add_server_key(host_key)
            server = _ScpServerInterface(validated_username, validated_password, emit_log)

            with counter_lock:
                session_counter["count"] += 1
            emit_log(f"[SCP 접속] {client_address[0]}:{client_address[1]}")
            emit_runtime()

            try:
                transport.start_server(server=server)
                channel = transport.accept(timeout=15)
                if channel is None:
                    raise RuntimeError("SCP 채널을 열지 못했습니다.")
                if not server.command_event.wait(timeout=15):
                    raise RuntimeError("SCP 실행 명령을 받지 못했습니다.")
                self._handle_command(
                    channel=channel,
                    command=server.command,
                    root_folder=validated_root,
                    runtime=runtime,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    emit_log=emit_log,
                )
            except Exception as exc:
                emit_log(f"[SCP 오류] {client_address[0]}:{client_address[1]} / {exc}")
            finally:
                transport.close()
                client_socket.close()
                with counter_lock:
                    session_counter["count"] = max(0, session_counter["count"] - 1)
                emit_log(f"[SCP 연결 종료] {client_address[0]}:{client_address[1]}")
                emit_runtime()

        emit_log(f"[SCP 서버 시작] {runtime.bind_host}:{runtime.port} / 루트 {runtime.root_folder}")
        emit_runtime()

        try:
            while cancel_event is None or not cancel_event.is_set():
                try:
                    client_socket, client_address = listener.accept()
                except socket.timeout:
                    continue
                worker = threading.Thread(target=handle_client, args=(client_socket, client_address), daemon=True)
                worker.start()
                worker_threads.append(worker)
        finally:
            listener.close()
            for worker in worker_threads:
                worker.join(timeout=1.5)
            emit_log("[SCP 서버 중지] 임시 SCP 서버를 종료했습니다.")

        return OperationResult(True, "SCP 임시 서버를 중지했습니다.")

    def ensure_host_key(self) -> Path:
        self.paths.ftp_keys_dir.mkdir(parents=True, exist_ok=True)
        key_path = self.paths.ftp_keys_dir / "scp-host.key"
        if not key_path.exists():
            key = paramiko.RSAKey.generate(2048)
            key.write_private_key_file(str(key_path))
        return key_path

    def _handle_command(
        self,
        channel,
        command: str,
        root_folder: Path,
        runtime: ScpServerRuntime,
        progress_callback,
        cancel_event,
        emit_log,
    ) -> None:
        tokens = shlex.split(command)
        if not tokens or tokens[0] != "scp":
            self._send_error(channel, "scp 명령만 지원합니다.")
            return

        flags = {token for token in tokens[1:] if token.startswith("-")}
        path_text = next((token for token in reversed(tokens[1:]) if not token.startswith("-")), ".")
        is_upload = any("t" in flag for flag in flags)
        is_download = any("f" in flag for flag in flags)
        target_is_dir = any("d" in flag for flag in flags)
        target_path = self._resolve_rooted_path(root_folder, path_text)

        if is_upload == is_download:
            self._send_error(channel, "지원하지 않는 SCP 명령 형식입니다.")
            return

        if is_upload:
            self._handle_upload_request(
                channel=channel,
                target_path=target_path,
                target_is_dir=target_is_dir,
                runtime=runtime,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                emit_log=emit_log,
            )
        else:
            self._handle_download_request(
                channel=channel,
                source_path=target_path,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                emit_log=emit_log,
            )

    def _handle_upload_request(
        self,
        channel,
        target_path: Path,
        target_is_dir: bool,
        runtime: ScpServerRuntime,
        progress_callback,
        cancel_event,
        emit_log,
    ) -> None:
        if runtime.read_only:
            self._send_error(channel, "읽기 전용 서버에서는 업로드를 허용하지 않습니다.")
            return

        if target_is_dir or str(target_path).endswith(os.sep):
            target_path.mkdir(parents=True, exist_ok=True)

        self._send_ack(channel)
        first_file = True
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self._send_error(channel, "서버가 중지되어 업로드를 취소했습니다.")
                return

            control, payload = self._read_command(channel)
            if control == "":
                break
            if control == "T":
                self._send_ack(channel)
                continue
            if control == "D":
                self._send_error(channel, "폴더 재귀 업로드는 아직 지원하지 않습니다.")
                return
            if control == "E":
                self._send_ack(channel)
                return
            if control != "C":
                self._send_error(channel, "지원하지 않는 SCP 업로드 헤더입니다.")
                return

            mode, size_text, filename = payload.split(" ", 2)
            size = int(size_text)
            if target_is_dir or target_path.exists() and target_path.is_dir():
                local_path = self._resolve_child_path(Path(runtime.root_folder), target_path, filename)
            elif first_file:
                local_path = target_path
            else:
                self._send_error(channel, "여러 파일 업로드에는 디렉터리 대상 경로가 필요합니다.")
                return

            local_path.parent.mkdir(parents=True, exist_ok=True)
            result = ScpTransferResult(
                action="수신",
                source_path=filename,
                target_path=str(local_path),
                size_bytes=size,
                status="진행 중",
            )
            self._send_ack(channel)
            started = time.perf_counter()
            transferred = 0
            with local_path.open("wb") as handle:
                while transferred < size:
                    chunk = channel.recv(min(self._CHUNK_SIZE, size - transferred))
                    if not chunk:
                        raise RuntimeError("SCP 업로드 수신 중 연결이 종료되었습니다.")
                    handle.write(chunk)
                    transferred += len(chunk)
                    result.transferred_bytes = transferred
                    result.duration_seconds = time.perf_counter() - started
                    self._emit_transfer(progress_callback, result)

            trailer = channel.recv(1)
            if trailer not in {b"\x00", b""}:
                if trailer and trailer[0] in {1, 2}:
                    raise RuntimeError(self._read_line(channel) or "SCP 업로드 오류")
                raise RuntimeError("SCP 업로드 종료 코드를 해석하지 못했습니다.")
            self._send_ack(channel)
            result.transferred_bytes = size
            result.duration_seconds = time.perf_counter() - started
            result.status = "완료"
            self._emit_transfer(progress_callback, result)
            emit_log(f"[SCP 업로드 수신] {local_path}")
            first_file = False

        channel.send_exit_status(0)

    def _handle_download_request(self, channel, source_path: Path, progress_callback, cancel_event, emit_log) -> None:
        if not source_path.exists():
            self._send_error(channel, f"경로를 찾을 수 없습니다: {source_path.name}")
            return
        if source_path.is_dir():
            self._send_error(channel, "폴더 다운로드는 아직 지원하지 않습니다.")
            return

        self._expect_client_ack(channel)
        size = source_path.stat().st_size
        mode = f"{source_path.stat().st_mode & 0o7777:04o}"
        result = ScpTransferResult(
            action="송신",
            source_path=str(source_path),
            target_path=source_path.name,
            size_bytes=size,
            status="진행 중",
        )
        header = f"C{mode} {size} {source_path.name}\n".encode("utf-8")
        channel.sendall(header)
        self._expect_client_ack(channel)

        started = time.perf_counter()
        transferred = 0
        with source_path.open("rb") as handle:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    self._send_error(channel, "서버가 중지되어 다운로드를 취소했습니다.")
                    return
                chunk = handle.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                channel.sendall(chunk)
                transferred += len(chunk)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)

        channel.sendall(b"\x00")
        self._expect_client_ack(channel)
        result.transferred_bytes = size
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        emit_log(f"[SCP 다운로드 송신] {source_path}")
        channel.send_exit_status(0)

    def _resolve_rooted_path(self, root_folder: Path, path_text: str) -> Path:
        normalized = str(path_text or ".").replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute():
            pure = PurePosixPath(*pure.parts[1:])
        candidate = (root_folder / Path(str(pure))).resolve()
        try:
            candidate.relative_to(root_folder.resolve())
        except ValueError as exc:
            raise RuntimeError("SCP 루트 폴더 밖 경로는 허용하지 않습니다.") from exc
        return candidate

    def _resolve_child_path(self, root_folder: Path, parent_path: Path, name: str) -> Path:
        candidate = (parent_path / name).resolve()
        try:
            candidate.relative_to(root_folder.resolve())
        except ValueError as exc:
            raise RuntimeError("SCP 루트 폴더 밖 경로는 허용하지 않습니다.") from exc
        return candidate

    def _read_command(self, channel) -> tuple[str, str]:
        code = channel.recv(1)
        if not code:
            return "", ""
        value = code[0]
        if value in {1, 2}:
            raise RuntimeError(self._read_line(channel) or "SCP 상대 오류")
        return chr(value), self._read_line(channel)

    def _expect_client_ack(self, channel) -> None:
        code = channel.recv(1)
        if not code:
            raise RuntimeError("SCP 클라이언트 응답이 없습니다.")
        value = code[0]
        if value == 0:
            return
        if value in {1, 2}:
            raise RuntimeError(self._read_line(channel) or "SCP 클라이언트 오류")
        raise RuntimeError(f"SCP 응답 코드 {value}를 처리할 수 없습니다.")

    def _read_line(self, channel) -> str:
        buffer = bytearray()
        while True:
            chunk = channel.recv(1)
            if not chunk or chunk == b"\n":
                break
            buffer.extend(chunk)
        return buffer.decode("utf-8", errors="replace").strip()

    def _send_ack(self, channel) -> None:
        channel.sendall(b"\x00")

    def _send_error(self, channel, message: str) -> None:
        channel.sendall(b"\x02" + message.encode("utf-8", errors="replace") + b"\n")
        try:
            channel.send_exit_status(1)
        except Exception:
            pass

    def _emit_transfer(self, progress_callback, result: ScpTransferResult) -> None:
        if progress_callback is not None:
            progress_callback.emit({"kind": "transfer", "result": result})

    def _friendly_bind_error(self, exc: OSError, port: int) -> str:
        if exc.errno in {errno.EADDRINUSE, 10048}:
            return f"포트 {port}가 이미 사용 중입니다."
        if exc.errno in {errno.EACCES, 10013}:
            return f"포트 {port}를 열 권한이 없습니다."
        return str(exc)

    def _display_host(self, bind_host: str) -> str:
        if bind_host and bind_host != "0.0.0.0":
            return bind_host
        try:
            candidates = socket.gethostbyname_ex(socket.gethostname())[2]
            for item in candidates:
                if item and not item.startswith("127."):
                    return item
        except OSError:
            pass
        return "127.0.0.1"

    def _host_key_fingerprint(self, key_path: Path) -> str:
        key = paramiko.RSAKey.from_private_key_file(str(key_path))
        digest = hashlib.sha256(key.asbytes()).hexdigest().upper()
        return "SHA256:" + digest

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
