from __future__ import annotations

import ftplib
import hashlib
import logging
import posixpath
import stat as stat_module
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import paramiko
except ImportError:  # pragma: no cover - optional dependency guard
    paramiko = None  # type: ignore[assignment]

from app.models.ftp_models import FtpRemoteEntry, FtpTransferResult
from app.models.result_models import OperationResult
from app.utils.file_utils import AppPaths, execution_environment_label, is_packaged_runtime
from app.utils.validators import (
    default_ftp_port,
    normalize_remote_path,
    parse_positive_int,
    validate_ftp_host,
    validate_ftp_protocol,
    validate_ftp_username,
    validate_remote_name,
)


class TransferCancelled(RuntimeError):
    pass


@dataclass(slots=True)
class _FtpClientSession:
    protocol: str
    client: Any
    transport: Any = None
    current_path: str = "/"
    lock: threading.RLock = field(default_factory=threading.RLock)


class FtpClientService:
    _CHUNK_SIZE = 64 * 1024

    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger
        self._sessions: dict[str, _FtpClientSession] = {}
        self._session_guard = threading.RLock()

    def runtime_support_status(self, protocol: str) -> OperationResult:
        normalized_protocol = validate_ftp_protocol(protocol)
        environment = execution_environment_label()
        dependency_name = "paramiko" if normalized_protocol == "sftp" and paramiko is None else ""

        if dependency_name:
            return OperationResult(
                False,
                (
                    f"{environment}: {normalized_protocol.upper()} 클라이언트를 사용하려면 requirements.txt 설치가 필요합니다."
                    if not is_packaged_runtime()
                    else f"{environment}: {normalized_protocol.upper()} 클라이언트 구성요소가 누락되었거나 손상되었을 수 있습니다."
                ),
                details=self._dependency_resolution_message(
                    feature_name=f"{normalized_protocol.upper()} 클라이언트",
                    dependency_name=dependency_name,
                ),
            )

        return OperationResult(
            True,
            f"{environment}: {normalized_protocol.upper()} 클라이언트 사용 준비가 완료되었습니다.",
            details="필수 런타임 의존성이 확인되었습니다.",
        )

    def connect(
        self,
        protocol: str,
        host: str,
        port: int | str | None,
        username: str,
        password: str,
        passive_mode: bool = True,
        timeout_seconds: int | str = 15,
        remote_path: str = "/",
        progress_callback=None,
    ) -> OperationResult:
        normalized_protocol = validate_ftp_protocol(protocol)
        validated_host = validate_ftp_host(host)
        validated_username = validate_ftp_username(username, normalized_protocol)
        validated_port = self._resolve_port(normalized_protocol, port, server_mode=False)
        validated_timeout = parse_positive_int(timeout_seconds, "FTP 타임아웃", minimum=1, maximum=300)
        target_path = normalize_remote_path(remote_path)

        if normalized_protocol == "ftp":
            client = ftplib.FTP(timeout=validated_timeout)
            client.connect(validated_host, validated_port, timeout=validated_timeout)
            client.login(validated_username, password)
            client.set_pasv(passive_mode)
            cwd, entries = self._ftp_list_directory(client, target_path)
            session = _FtpClientSession(protocol="ftp", client=client, current_path=cwd)
            fingerprint = ""
        elif normalized_protocol == "ftps":
            client = ftplib.FTP_TLS(timeout=validated_timeout)
            client.connect(validated_host, validated_port, timeout=validated_timeout)
            client.login(validated_username, password)
            client.prot_p()
            client.set_pasv(passive_mode)
            cwd, entries = self._ftp_list_directory(client, target_path)
            session = _FtpClientSession(protocol="ftps", client=client, current_path=cwd)
            fingerprint = ""
        else:
            self._ensure_paramiko_support()
            transport = paramiko.Transport((validated_host, validated_port))
            transport.banner_timeout = validated_timeout
            transport.connect(username=validated_username, password=password)
            client = paramiko.SFTPClient.from_transport(transport)
            cwd, entries = self._sftp_list_directory(client, target_path)
            session = _FtpClientSession(
                protocol="sftp",
                client=client,
                transport=transport,
                current_path=cwd,
            )
            fingerprint = self._fingerprint_host_key(transport)

        session_id = uuid.uuid4().hex
        with self._session_guard:
            self._sessions[session_id] = session

        self.logger.info(
            "FTP client connected: %s://%s:%s as %s",
            normalized_protocol,
            validated_host,
            validated_port,
            validated_username,
        )
        self._emit_log(
            progress_callback,
            f"[연결] {normalized_protocol.upper()} {validated_host}:{validated_port} / 사용자 {validated_username}",
        )
        message = f"{normalized_protocol.upper()} 연결을 완료했습니다."
        if normalized_protocol == "sftp" and fingerprint:
            message += f" 서버 키 지문 {fingerprint}"
        return OperationResult(
            True,
            message,
            payload={
                "session_id": session_id,
                "cwd": cwd,
                "entries": entries,
                "protocol": normalized_protocol,
                "host_key_fingerprint": fingerprint,
            },
        )

    def disconnect(self, session_id: str) -> OperationResult:
        session = self._pop_session(session_id)
        with session.lock:
            self._close_session(session)
        self.logger.info("FTP client disconnected: %s", session_id)
        return OperationResult(True, "FTP 연결을 종료했습니다.")

    def list_directory(
        self,
        session_id: str,
        remote_path: str = "",
        progress_callback=None,
    ) -> OperationResult:
        session = self._get_session(session_id)
        requested = normalize_remote_path(remote_path or session.current_path)
        with session.lock:
            if session.protocol in {"ftp", "ftps"}:
                cwd, entries = self._ftp_list_directory(session.client, requested)
            else:
                cwd, entries = self._sftp_list_directory(session.client, requested)
            session.current_path = cwd
        self._emit_log(progress_callback, f"[목록] {cwd} / {len(entries)}개")
        return OperationResult(
            True,
            f"{cwd} 목록을 불러왔습니다.",
            payload={"cwd": cwd, "entries": entries},
        )

    def upload_files(
        self,
        session_id: str,
        local_paths: list[str],
        remote_dir: str,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        session = self._get_session(session_id)
        target_dir = normalize_remote_path(remote_dir or session.current_path)
        source_paths = [Path(path) for path in local_paths if str(path).strip()]
        if not source_paths:
            raise ValueError("업로드할 파일을 선택해 주세요.")

        results: list[FtpTransferResult] = []
        with session.lock:
            for local_path in source_paths:
                if cancel_event and cancel_event.is_set():
                    break
                if not local_path.exists() or not local_path.is_file():
                    raise ValueError(f"업로드 대상 파일이 없습니다: {local_path}")
                remote_path = self._join_remote(target_dir, local_path.name)
                self._emit_log(progress_callback, f"[업로드 시작] {local_path} -> {remote_path}")
                if session.protocol in {"ftp", "ftps"}:
                    result = self._ftp_upload(session.client, local_path, remote_path, progress_callback, cancel_event)
                else:
                    result = self._sftp_upload(session.client, local_path, remote_path, progress_callback, cancel_event)
                results.append(result)
                self._emit_log(progress_callback, f"[업로드 완료] {remote_path} ({result.size_text} bytes)")

        session.current_path = target_dir
        completed = sum(1 for item in results if item.status == "완료")
        return OperationResult(True, f"{completed}개 파일 업로드를 마쳤습니다.", payload=results)

    def download_files(
        self,
        session_id: str,
        entries: list[FtpRemoteEntry],
        local_dir: str,
        progress_callback=None,
        cancel_event=None,
    ) -> OperationResult:
        session = self._get_session(session_id)
        local_root = Path(local_dir).expanduser()
        local_root.mkdir(parents=True, exist_ok=True)
        if not entries:
            raise ValueError("다운로드할 항목을 선택해 주세요.")

        results: list[FtpTransferResult] = []
        with session.lock:
            for entry in entries:
                if cancel_event and cancel_event.is_set():
                    break
                if entry.is_dir:
                    raise ValueError("v1에서는 폴더 다운로드를 지원하지 않습니다.")
                local_path = local_root / entry.name
                self._emit_log(progress_callback, f"[다운로드 시작] {entry.remote_path} -> {local_path}")
                if session.protocol in {"ftp", "ftps"}:
                    result = self._ftp_download(session.client, entry, local_path, progress_callback, cancel_event)
                else:
                    result = self._sftp_download(session.client, entry, local_path, progress_callback, cancel_event)
                results.append(result)
                self._emit_log(progress_callback, f"[다운로드 완료] {entry.remote_path} ({result.size_text} bytes)")

        completed = sum(1 for item in results if item.status == "완료")
        return OperationResult(True, f"{completed}개 파일 다운로드를 마쳤습니다.", payload=results)

    def make_directory(
        self,
        session_id: str,
        current_dir: str,
        folder_name: str,
        progress_callback=None,
    ) -> OperationResult:
        session = self._get_session(session_id)
        validated_name = validate_remote_name(folder_name, "원격 폴더 이름")
        remote_path = self._join_remote(current_dir or session.current_path, validated_name)
        with session.lock:
            if session.protocol in {"ftp", "ftps"}:
                session.client.mkd(remote_path)
            else:
                session.client.mkdir(remote_path)
        self._emit_log(progress_callback, f"[폴더 생성] {remote_path}")
        return OperationResult(True, f"{validated_name} 폴더를 만들었습니다.", payload={"remote_path": remote_path})

    def rename_path(
        self,
        session_id: str,
        source_path: str,
        new_name: str,
        progress_callback=None,
    ) -> OperationResult:
        session = self._get_session(session_id)
        source = normalize_remote_path(source_path)
        target_name = validate_remote_name(new_name, "새 이름")
        target_path = self._join_remote(posixpath.dirname(source) or "/", target_name)
        with session.lock:
            session.client.rename(source, target_path)
        self._emit_log(progress_callback, f"[이름 변경] {source} -> {target_path}")
        return OperationResult(True, "이름을 변경했습니다.", payload={"remote_path": target_path})

    def delete_entries(
        self,
        session_id: str,
        entries: list[FtpRemoteEntry],
        progress_callback=None,
    ) -> OperationResult:
        session = self._get_session(session_id)
        if not entries:
            raise ValueError("삭제할 항목을 선택해 주세요.")

        with session.lock:
            for entry in entries:
                if entry.is_dir:
                    if session.protocol in {"ftp", "ftps"}:
                        session.client.rmd(entry.remote_path)
                    else:
                        session.client.rmdir(entry.remote_path)
                else:
                    if session.protocol in {"ftp", "ftps"}:
                        session.client.delete(entry.remote_path)
                    else:
                        session.client.remove(entry.remote_path)
                self._emit_log(progress_callback, f"[삭제] {entry.remote_path}")
        return OperationResult(True, f"{len(entries)}개 항목을 삭제했습니다.")

    def _resolve_port(self, protocol: str, raw_port: int | str | None, server_mode: bool) -> int:
        if raw_port in (None, ""):
            return default_ftp_port(protocol, server_mode=server_mode)
        return parse_positive_int(raw_port, "포트", minimum=1, maximum=65535)

    def _get_session(self, session_id: str) -> _FtpClientSession:
        with self._session_guard:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("유효한 FTP 세션이 없습니다. 다시 연결해 주세요.")
        return session

    def _pop_session(self, session_id: str) -> _FtpClientSession:
        with self._session_guard:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise ValueError("유효한 FTP 세션이 없습니다.")
        return session

    def _close_session(self, session: _FtpClientSession) -> None:
        if session.protocol == "sftp":
            try:
                session.client.close()
            finally:
                if session.transport is not None:
                    session.transport.close()
            return
        try:
            session.client.quit()
        except Exception:
            try:
                session.client.close()
            except Exception:
                pass

    def _ftp_list_directory(self, client: ftplib.FTP, remote_path: str) -> tuple[str, list[FtpRemoteEntry]]:
        client.cwd(remote_path)
        cwd = normalize_remote_path(client.pwd())
        entries: list[FtpRemoteEntry] = []
        try:
            for name, facts in client.mlsd():
                if name in {".", ".."}:
                    continue
                entry_type = "dir" if str(facts.get("type", "")).lower() == "dir" else "file"
                size = int(facts.get("size", 0) or 0)
                modified = self._format_mlsx_timestamp(str(facts.get("modify", "") or ""))
                permissions = str(facts.get("perm", "") or "")
                entries.append(
                    FtpRemoteEntry(
                        name=name,
                        entry_type=entry_type,
                        size_bytes=size,
                        modified_at=modified,
                        permissions=permissions,
                        remote_path=self._join_remote(cwd, name),
                    )
                )
        except Exception:
            lines: list[str] = []
            client.retrlines("LIST", lines.append)
            entries = self._parse_ftp_list_lines(lines, cwd)
        return cwd, sorted(entries, key=lambda item: (item.entry_type != "dir", item.name.lower()))

    def _sftp_list_directory(
        self,
        client: paramiko.SFTPClient,
        remote_path: str,
    ) -> tuple[str, list[FtpRemoteEntry]]:
        cwd = normalize_remote_path(client.normalize(remote_path))
        entries: list[FtpRemoteEntry] = []
        for attr in client.listdir_attr(cwd):
            if attr.filename in {".", ".."}:
                continue
            permissions = stat_module.filemode(attr.st_mode)
            entry_type = "dir" if stat_module.S_ISDIR(attr.st_mode) else "file"
            modified = datetime.fromtimestamp(attr.st_mtime).strftime("%Y-%m-%d %H:%M:%S") if attr.st_mtime else ""
            entries.append(
                FtpRemoteEntry(
                    name=attr.filename,
                    entry_type=entry_type,
                    size_bytes=int(attr.st_size or 0),
                    modified_at=modified,
                    permissions=permissions,
                    remote_path=self._join_remote(cwd, attr.filename),
                )
            )
        return cwd, sorted(entries, key=lambda item: (item.entry_type != "dir", item.name.lower()))

    def _ftp_upload(
        self,
        client: ftplib.FTP,
        local_path: Path,
        remote_path: str,
        progress_callback,
        cancel_event,
    ) -> FtpTransferResult:
        total = local_path.stat().st_size
        started = time.perf_counter()
        transferred = 0
        result = FtpTransferResult(
            action="업로드",
            source_path=str(local_path),
            target_path=remote_path,
            size_bytes=total,
            status="진행 중",
        )

        with local_path.open("rb") as handle:
            def callback(block: bytes) -> None:
                nonlocal transferred
                transferred += len(block)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)
                if cancel_event and cancel_event.is_set():
                    raise TransferCancelled()

            try:
                client.storbinary(f"STOR {remote_path}", handle, blocksize=self._CHUNK_SIZE, callback=callback)
            except TransferCancelled:
                try:
                    client.abort()
                except Exception:
                    pass
                result.status = "중지"
                result.error = "사용자가 전송을 중지했습니다."
                return result

        result.transferred_bytes = total
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        return result

    def _ftp_download(
        self,
        client: ftplib.FTP,
        entry: FtpRemoteEntry,
        local_path: Path,
        progress_callback,
        cancel_event,
    ) -> FtpTransferResult:
        started = time.perf_counter()
        transferred = 0
        result = FtpTransferResult(
            action="다운로드",
            source_path=entry.remote_path,
            target_path=str(local_path),
            size_bytes=entry.size_bytes,
            status="진행 중",
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)

        with local_path.open("wb") as handle:
            def callback(block: bytes) -> None:
                nonlocal transferred
                if cancel_event and cancel_event.is_set():
                    raise TransferCancelled()
                handle.write(block)
                transferred += len(block)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)

            try:
                client.retrbinary(f"RETR {entry.remote_path}", callback, blocksize=self._CHUNK_SIZE)
            except TransferCancelled:
                try:
                    client.abort()
                except Exception:
                    pass
                handle.close()
                local_path.unlink(missing_ok=True)
                result.status = "중지"
                result.error = "사용자가 전송을 중지했습니다."
                return result

        result.transferred_bytes = transferred
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        return result

    def _sftp_upload(
        self,
        client: paramiko.SFTPClient,
        local_path: Path,
        remote_path: str,
        progress_callback,
        cancel_event,
    ) -> FtpTransferResult:
        total = local_path.stat().st_size
        started = time.perf_counter()
        transferred = 0
        result = FtpTransferResult(
            action="업로드",
            source_path=str(local_path),
            target_path=remote_path,
            size_bytes=total,
            status="진행 중",
        )
        with local_path.open("rb") as source, client.open(remote_path, "wb") as target:
            while True:
                if cancel_event and cancel_event.is_set():
                    target.close()
                    try:
                        client.remove(remote_path)
                    except Exception:
                        pass
                    result.status = "중지"
                    result.error = "사용자가 전송을 중지했습니다."
                    return result
                chunk = source.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                transferred += len(chunk)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)
        result.transferred_bytes = total
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        return result

    def _sftp_download(
        self,
        client: paramiko.SFTPClient,
        entry: FtpRemoteEntry,
        local_path: Path,
        progress_callback,
        cancel_event,
    ) -> FtpTransferResult:
        started = time.perf_counter()
        transferred = 0
        result = FtpTransferResult(
            action="다운로드",
            source_path=entry.remote_path,
            target_path=str(local_path),
            size_bytes=entry.size_bytes,
            status="진행 중",
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with client.open(entry.remote_path, "rb") as source, local_path.open("wb") as target:
            while True:
                if cancel_event and cancel_event.is_set():
                    target.close()
                    local_path.unlink(missing_ok=True)
                    result.status = "중지"
                    result.error = "사용자가 전송을 중지했습니다."
                    return result
                chunk = source.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                transferred += len(chunk)
                result.transferred_bytes = transferred
                result.duration_seconds = time.perf_counter() - started
                self._emit_transfer(progress_callback, result)
        result.transferred_bytes = transferred
        result.duration_seconds = time.perf_counter() - started
        result.status = "완료"
        self._emit_transfer(progress_callback, result)
        return result

    def _emit_transfer(self, progress_callback, result: FtpTransferResult) -> None:
        if progress_callback is not None:
            progress_callback.emit({"kind": "transfer", "result": result})

    def _emit_log(self, progress_callback, message: str) -> None:
        if progress_callback is not None:
            progress_callback.emit({"kind": "log", "message": message})

    def _join_remote(self, current_dir: str, name: str) -> str:
        base = normalize_remote_path(current_dir)
        joined = posixpath.join(base.rstrip("/") or "/", name)
        return normalize_remote_path(joined)

    def _format_mlsx_timestamp(self, value: str) -> str:
        if not value:
            return ""
        try:
            return datetime.strptime(value[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value

    def _fingerprint_host_key(self, transport: paramiko.Transport) -> str:
        key = transport.get_remote_server_key()
        if key is None:
            return ""
        digest = hashlib.sha256(key.asbytes()).hexdigest().upper()
        return "SHA256:" + digest

    def _ensure_paramiko_support(self) -> None:
        if paramiko is None:
            raise RuntimeError("SFTP 기능을 사용하려면 paramiko 설치가 필요합니다.")

    def _parse_ftp_list_lines(self, lines: list[str], cwd: str) -> list[FtpRemoteEntry]:
        return self.__parse_ftp_list_lines_impl(lines, cwd)

    def _ensure_paramiko_support(self) -> None:
        if paramiko is None:
            support = self.runtime_support_status("sftp")
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

    def __parse_ftp_list_lines_impl(self, lines: list[str], cwd: str) -> list[FtpRemoteEntry]:
        entries: list[FtpRemoteEntry] = []
        for line in lines:
            parsed = self._parse_ftp_list_line(line, cwd)
            if parsed is not None:
                entries.append(parsed)
        return entries

    def _parse_ftp_list_line(self, line: str, cwd: str) -> FtpRemoteEntry | None:
        text = line.strip()
        if not text:
            return None
        unix_parts = text.split(maxsplit=8)
        if len(unix_parts) >= 9 and unix_parts[0][0] in {"d", "-", "l"}:
            permissions = unix_parts[0]
            entry_type = "dir" if permissions.startswith("d") else "file"
            try:
                size = int(unix_parts[4])
            except ValueError:
                size = 0
            modified = " ".join(unix_parts[5:8])
            name = unix_parts[8]
            return FtpRemoteEntry(
                name=name,
                entry_type=entry_type,
                size_bytes=size,
                modified_at=modified,
                permissions=permissions,
                remote_path=self._join_remote(cwd, name),
            )
        if len(text) > 17 and text[2] == "-" and text[5] == "-":
            parts = text.split(maxsplit=3)
            if len(parts) == 4:
                date_text, time_text, marker, name = parts
                entry_type = "dir" if marker.upper() == "<DIR>" else "file"
                try:
                    size = 0 if entry_type == "dir" else int(marker.replace(",", ""))
                except ValueError:
                    size = 0
                return FtpRemoteEntry(
                    name=name,
                    entry_type=entry_type,
                    size_bytes=size,
                    modified_at=f"{date_text} {time_text}",
                    permissions="",
                    remote_path=self._join_remote(cwd, name),
                )
        return None
