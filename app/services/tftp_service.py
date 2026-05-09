from __future__ import annotations

import errno
import logging
import threading
import time
from pathlib import Path, PurePosixPath

try:
    from tftpy import TftpClient, TftpPacketDAT, TftpServer
except ImportError:  # pragma: no cover - optional dependency guard
    TftpClient = None  # type: ignore[assignment]
    TftpPacketDAT = object  # type: ignore[assignment]
    TftpServer = None  # type: ignore[assignment]

from app.models.result_models import OperationResult
from app.models.tftp_models import TftpServerRuntime, TftpTransferResult
from app.utils.file_utils import AppPaths, execution_environment_label, is_packaged_runtime
from app.utils.validators import (
    parse_positive_int,
    require_text,
    validate_existing_directory,
    validate_ftp_host,
    validate_optional_ipv4,
)


class TftpTransferCancelled(RuntimeError):
    pass


class _TftpProgressLogHandler(logging.Handler):
    def __init__(self, callback) -> None:
        super().__init__(level=logging.INFO)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().strip()
        if message:
            self._callback(message)


class TftpService:
    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger

    def runtime_support_status(self) -> OperationResult:
        environment = execution_environment_label()
        if TftpClient is None or TftpServer is None:
            return OperationResult(
                False,
                (
                    f"{environment}: TFTP 기능을 사용하려면 requirements.txt 설치가 필요합니다."
                    if not is_packaged_runtime()
                    else f"{environment}: TFTP 구성요소가 누락되었거나 손상되었을 수 있습니다."
                ),
                details=self._dependency_resolution_message("TFTP", "tftpy"),
            )
        return OperationResult(
            True,
            f"{environment}: TFTP 사용 준비가 완료되었습니다.",
            details="필수 런타임 의존성이 확인되었습니다.",
        )

    def download_file(
        self,
        host: str,
        port: int | str | None,
        remote_path: str,
        local_folder: str,
        timeout_seconds: int | str = 5,
        retries: int | str = 3,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        self._ensure_runtime_support()
        validated_host = validate_ftp_host(host)
        validated_port = parse_positive_int(port or 69, "TFTP 포트", minimum=1, maximum=65535)
        validated_timeout = parse_positive_int(timeout_seconds, "TFTP 타임아웃", minimum=1, maximum=120)
        validated_retries = parse_positive_int(retries, "재시도", minimum=0, maximum=20)
        normalized_remote = self._normalize_remote_path(remote_path)
        target_dir = Path(local_folder).expanduser()
        if not str(target_dir).strip():
            raise ValueError("로컬 폴더를 입력해 주세요.")
        target_dir.mkdir(parents=True, exist_ok=True)

        local_name = PurePosixPath(normalized_remote).name or "download.bin"
        local_path = target_dir / local_name
        result = TftpTransferResult(
            action="다운로드",
            source_path=normalized_remote,
            target_path=str(local_path),
            status="진행 중",
        )
        seen_blocks: set[int] = set()
        transferred = 0
        started = time.perf_counter()

        def packethook(packet) -> None:
            nonlocal transferred
            if cancel_event is not None and cancel_event.is_set():
                raise TftpTransferCancelled()
            block_number = getattr(packet, "blocknumber", None)
            data = getattr(packet, "data", b"")
            if isinstance(packet, TftpPacketDAT) and block_number not in seen_blocks:
                seen_blocks.add(block_number)
                transferred += len(data or b"")
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)

        self._emit_log(progress_callback, f"[TFTP 다운로드 시작] {validated_host}:{validated_port} / {normalized_remote}")
        client = TftpClient(validated_host, validated_port)
        try:
            client.download(
                normalized_remote,
                str(local_path),
                packethook=packethook,
                timeout=validated_timeout,
                retries=validated_retries,
            )
        except TftpTransferCancelled:
            local_path.unlink(missing_ok=True)
            result.status = "중지"
            result.error = "사용자 중지"
            result.duration_seconds = time.perf_counter() - started
            self._emit_transfer(progress_callback, result)
            return OperationResult(True, "TFTP 다운로드를 중지했습니다.", payload=[result])

        result.size_bytes = local_path.stat().st_size if local_path.exists() else result.transferred_bytes
        result.transferred_bytes = result.size_bytes
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        self._emit_log(progress_callback, f"[TFTP 다운로드 완료] {local_path}")
        return OperationResult(True, "TFTP 다운로드를 마쳤습니다.", payload=[result])

    def upload_file(
        self,
        host: str,
        port: int | str | None,
        local_path: str,
        remote_path: str,
        timeout_seconds: int | str = 5,
        retries: int | str = 3,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        self._ensure_runtime_support()
        validated_host = validate_ftp_host(host)
        validated_port = parse_positive_int(port or 69, "TFTP 포트", minimum=1, maximum=65535)
        validated_timeout = parse_positive_int(timeout_seconds, "TFTP 타임아웃", minimum=1, maximum=120)
        validated_retries = parse_positive_int(retries, "재시도", minimum=0, maximum=20)
        source_path = Path(require_text(local_path, "업로드 파일")).expanduser()
        if not source_path.exists() or not source_path.is_file():
            raise ValueError(f"업로드 파일이 없습니다: {source_path}")
        normalized_remote = self._normalize_remote_path(remote_path or source_path.name)
        result = TftpTransferResult(
            action="업로드",
            source_path=str(source_path),
            target_path=normalized_remote,
            size_bytes=source_path.stat().st_size,
            status="진행 중",
        )
        seen_blocks: set[int] = set()
        transferred = 0
        started = time.perf_counter()

        def packethook(packet) -> None:
            nonlocal transferred
            if cancel_event is not None and cancel_event.is_set():
                raise TftpTransferCancelled()
            block_number = getattr(packet, "blocknumber", None)
            data = getattr(packet, "data", b"")
            if isinstance(packet, TftpPacketDAT) and block_number not in seen_blocks:
                seen_blocks.add(block_number)
                transferred += len(data or b"")
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)

        self._emit_log(progress_callback, f"[TFTP 업로드 시작] {source_path} -> {validated_host}:{validated_port}/{normalized_remote}")
        client = TftpClient(validated_host, validated_port)
        try:
            client.upload(
                normalized_remote,
                str(source_path),
                packethook=packethook,
                timeout=validated_timeout,
                retries=validated_retries,
            )
        except TftpTransferCancelled:
            result.status = "중지"
            result.error = "사용자 중지"
            result.duration_seconds = time.perf_counter() - started
            self._emit_transfer(progress_callback, result)
            return OperationResult(True, "TFTP 업로드를 중지했습니다.", payload=[result])

        result.transferred_bytes = result.size_bytes
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        self._emit_log(progress_callback, f"[TFTP 업로드 완료] {normalized_remote}")
        return OperationResult(True, "TFTP 업로드를 마쳤습니다.", payload=[result])

    def run_temporary_server(
        self,
        bind_host: str,
        port: int | str | None,
        root_folder: str,
        read_only: bool,
        cancel_event=None,
        progress_callback=None,
    ) -> OperationResult:
        self._ensure_runtime_support()
        actual_bind_host = validate_optional_ipv4(bind_host, "바인드 IP") or "0.0.0.0"
        validated_port = parse_positive_int(port or 69, "TFTP 서버 포트", minimum=1, maximum=65535)
        validated_root = validate_existing_directory(root_folder, "공유 루트 폴더")
        runtime = TftpServerRuntime(
            bind_host=self._display_host(actual_bind_host),
            port=validated_port,
            root_folder=validated_root,
            read_only=bool(read_only),
            session_count=0,
        )

        server = TftpServer(
            tftproot=validated_root,
            upload_open=self._build_upload_handler(validated_root, bool(read_only), progress_callback),
        )
        server_logger = logging.getLogger("tftpy")
        relay_handler = _TftpProgressLogHandler(lambda message: self._emit_server_log(progress_callback, message))
        previous_level = server_logger.level
        server_logger.addHandler(relay_handler)
        server_logger.setLevel(logging.INFO)

        stop_event = threading.Event()

        def monitor_stop() -> None:
            if cancel_event is None:
                return
            while not cancel_event.is_set():
                time.sleep(0.2)
            stop_event.set()
            try:
                server.stop(now=True)
            except Exception:
                pass

        monitor_thread = threading.Thread(target=monitor_stop, daemon=True)
        monitor_thread.start()
        self._emit_server_log(
            progress_callback,
            f"[TFTP 서버 시작] {runtime.bind_host}:{runtime.port} / 루트 {runtime.root_folder}",
        )
        self._emit_server_runtime(progress_callback, runtime)
        try:
            server.listen(listenip=actual_bind_host, listenport=validated_port, timeout=1, retries=3)
        finally:
            stop_event.set()
            try:
                server.stop(now=True)
            except Exception:
                pass
            monitor_thread.join(timeout=1.0)
            server_logger.removeHandler(relay_handler)
            server_logger.setLevel(previous_level)
            self._emit_server_log(progress_callback, "[TFTP 서버 중지] 임시 TFTP 서버를 종료했습니다.")

        return OperationResult(True, "TFTP 임시 서버를 중지했습니다.")

    def _build_upload_handler(self, root_folder: str, read_only: bool, progress_callback):
        root_path = Path(root_folder).resolve()

        def upload_open(path: str, context):
            if read_only:
                raise OSError(errno.EPERM, "읽기 전용 서버에서는 업로드를 허용하지 않습니다.")
            local_path = self._resolve_rooted_path(root_path, path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._emit_server_log(progress_callback, f"[TFTP 업로드 수신] {local_path}")
            return local_path.open("wb")

        return upload_open

    def _resolve_rooted_path(self, root_folder: Path, remote_path: str) -> Path:
        normalized = PurePosixPath(self._normalize_remote_path(remote_path))
        candidate = (root_folder / Path(str(normalized))).resolve()
        try:
            candidate.relative_to(root_folder)
        except ValueError as exc:
            raise PermissionError("공유 루트 밖의 경로는 사용할 수 없습니다.") from exc
        return candidate

    def _normalize_remote_path(self, value: str) -> str:
        text = require_text(value, "원격 경로").replace("\\", "/").strip()
        normalized = str(PurePosixPath(text)).strip("/")
        if not normalized:
            raise ValueError("원격 경로를 입력해 주세요.")
        return normalized

    def _display_host(self, value: str) -> str:
        return "0.0.0.0" if value == "0.0.0.0" else value

    def _dependency_resolution_message(self, feature_name: str, dependency_name: str) -> str:
        if is_packaged_runtime():
            return (
                f"{feature_name} 구성요소가 설치본에 포함되지 않았을 수 있습니다.\n"
                "설치본을 다시 설치하거나 최신 버전을 내려받아 확인해 주세요."
            )
        return f"{feature_name} 기능을 사용하려면 `python -m pip install {dependency_name}` 또는 `pip install -r requirements.txt`를 실행해 주세요."

    def _ensure_runtime_support(self) -> None:
        if TftpClient is None or TftpServer is None:
            raise RuntimeError("TFTP 기능을 사용하려면 tftpy 설치가 필요합니다.")

    def _emit_log(self, progress_callback, message: str) -> None:
        self.logger.info(message)
        if progress_callback is not None:
            progress_callback.emit({"kind": "log", "message": message})

    def _emit_transfer(self, progress_callback, result: TftpTransferResult) -> None:
        if progress_callback is not None:
            progress_callback.emit({"kind": "transfer", "result": result})

    def _emit_server_log(self, progress_callback, message: str) -> None:
        self.logger.info(message)
        if progress_callback is not None:
            progress_callback.emit({"kind": "server_log", "message": message})

    def _emit_server_runtime(self, progress_callback, runtime: TftpServerRuntime) -> None:
        if progress_callback is not None:
            progress_callback.emit({"kind": "server_runtime", "runtime": runtime})
