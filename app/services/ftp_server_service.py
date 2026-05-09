from __future__ import annotations

import errno
import hashlib
import logging
import os
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable

try:
    import paramiko
except ImportError:  # pragma: no cover - optional dependency guard
    paramiko = None  # type: ignore[assignment]
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer
except ImportError:  # pragma: no cover - optional dependency guard
    DummyAuthorizer = None  # type: ignore[assignment]
    FTPHandler = object  # type: ignore[assignment]
    TLS_FTPHandler = object  # type: ignore[assignment]
    ThreadedFTPServer = None  # type: ignore[assignment]

from app.models.ftp_models import FtpServerRuntime
from app.models.result_models import OperationResult
from app.utils.file_utils import AppPaths, execution_environment_label, is_packaged_runtime
from app.utils.validators import (
    default_ftp_port,
    parse_positive_int,
    require_text,
    validate_existing_directory,
    validate_ftp_protocol,
    validate_ftp_username,
    validate_optional_ipv4,
)


if paramiko is not None:
    class _RootedSFTPHandle(paramiko.SFTPHandle):
        def stat(self):
            if getattr(self, "readfile", None) is not None:
                return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
            if getattr(self, "writefile", None) is not None:
                return paramiko.SFTPAttributes.from_stat(os.fstat(self.writefile.fileno()))
            return paramiko.SFTP_OP_UNSUPPORTED


    class _RootedSFTPServer(paramiko.SFTPServerInterface):
        def __init__(
            self,
            server,
            root_dir: str,
            read_only: bool,
            log_callback: Callable[[str], None] | None = None,
            *args,
            **kwargs,
        ):
            super().__init__(server, *args, **kwargs)
            self.root_dir = Path(root_dir).resolve()
            self.read_only = read_only
            self.log_callback = log_callback

        def _log(self, text: str) -> None:
            if self.log_callback is not None:
                self.log_callback(text)

        def _to_local_path(self, remote_path: str) -> Path:
            normalized = "/" + str(PurePosixPath(remote_path or "/")).lstrip("/")
            relative = PurePosixPath(normalized).relative_to("/")
            candidate = (self.root_dir / Path(str(relative))).resolve()
            try:
                candidate.relative_to(self.root_dir)
            except ValueError as exc:
                raise PermissionError("FTP 루트 밖의 경로에는 접근할 수 없습니다.") from exc
            return candidate

        def _attributes(self, path: Path, filename: str = "") -> paramiko.SFTPAttributes:
            attributes = paramiko.SFTPAttributes.from_stat(path.stat())
            attributes.filename = filename or path.name
            return attributes

        def _deny_if_readonly(self) -> None:
            if self.read_only:
                raise PermissionError("읽기 전용 서버에서는 변경 작업을 할 수 없습니다.")

        def list_folder(self, path: str):
            try:
                local_path = self._to_local_path(path)
                entries: list[paramiko.SFTPAttributes] = []
                for item in local_path.iterdir():
                    entries.append(self._attributes(item, item.name))
                return entries
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def stat(self, path: str):
            try:
                return self._attributes(self._to_local_path(path))
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def lstat(self, path: str):
            try:
                local_path = self._to_local_path(path)
                attributes = paramiko.SFTPAttributes.from_stat(os.lstat(local_path))
                attributes.filename = local_path.name
                return attributes
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def canonicalize(self, path: str):
            try:
                local_path = self._to_local_path(path)
                relative = local_path.relative_to(self.root_dir)
                return "/" + str(PurePosixPath(relative.as_posix()))
            except Exception:
                return "/"

        def open(self, path: str, flags: int, attr):
            try:
                self._deny_if_readonly_if_needed(flags)
                local_path = self._to_local_path(path)
                if flags & os.O_CREAT:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                mode = self._flags_to_mode(flags)
                file_obj = open(local_path, mode)
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

            handle = _RootedSFTPHandle(flags)
            handle.filename = str(local_path)
            if "r" in mode or "+" in mode:
                handle.readfile = file_obj
            if any(token in mode for token in ("w", "a", "+")):
                handle.writefile = file_obj
            return handle

        def remove(self, path: str):
            try:
                self._deny_if_readonly()
                os.remove(self._to_local_path(path))
                self._log(f"[SFTP 삭제] {path}")
                return paramiko.SFTP_OK
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def rename(self, oldpath: str, newpath: str):
            try:
                self._deny_if_readonly()
                os.replace(self._to_local_path(oldpath), self._to_local_path(newpath))
                self._log(f"[SFTP 이름 변경] {oldpath} -> {newpath}")
                return paramiko.SFTP_OK
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def mkdir(self, path: str, attr):
            try:
                self._deny_if_readonly()
                self._to_local_path(path).mkdir(parents=False, exist_ok=False)
                self._log(f"[SFTP 폴더 생성] {path}")
                return paramiko.SFTP_OK
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def rmdir(self, path: str):
            try:
                self._deny_if_readonly()
                self._to_local_path(path).rmdir()
                self._log(f"[SFTP 폴더 삭제] {path}")
                return paramiko.SFTP_OK
            except OSError as exc:
                return paramiko.SFTPServer.convert_errno(exc.errno)
            except PermissionError:
                return paramiko.SFTP_PERMISSION_DENIED

        def _flags_to_mode(self, flags: int) -> str:
            if flags & os.O_RDWR:
                if flags & os.O_CREAT and flags & os.O_TRUNC:
                    return "w+b"
                return "r+b"
            if flags & os.O_WRONLY:
                if flags & os.O_APPEND:
                    return "ab"
                return "wb"
            return "rb"

        def _deny_if_readonly_if_needed(self, flags: int) -> None:
            write_requested = any(
                flags & value
                for value in (os.O_WRONLY, os.O_RDWR, os.O_APPEND, os.O_CREAT, os.O_TRUNC)
            )
            if write_requested:
                self._deny_if_readonly()


    class _SFTPAuthServer(paramiko.ServerInterface):
        def __init__(self, username: str, password: str, on_log: Callable[[str], None]) -> None:
            self.username = username
            self.password = password
            self.on_log = on_log

        def check_auth_password(self, username: str, password: str):
            if username == self.username and password == self.password:
                self.on_log(f"[SFTP 로그인] {username}")
                return paramiko.AUTH_SUCCESSFUL
            self.on_log(f"[SFTP 인증 실패] {username}")
            return paramiko.AUTH_FAILED

        def get_allowed_auths(self, username: str):
            return "password"

        def check_channel_request(self, kind: str, chanid: int):
            if kind == "session":
                return paramiko.OPEN_SUCCEEDED
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
else:  # pragma: no cover - import fallback
    class _RootedSFTPHandle:
        pass


    class _RootedSFTPServer:
        pass


    class _SFTPAuthServer:
        def __init__(self, *args, **kwargs) -> None:
            pass


class FtpServerService:
    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger

    def runtime_support_status(self, protocol: str) -> OperationResult:
        normalized_protocol = validate_ftp_protocol(protocol)
        environment = execution_environment_label()
        missing_dependencies: list[str] = []

        if normalized_protocol in {"ftp", "ftps"} and (DummyAuthorizer is None or ThreadedFTPServer is None):
            missing_dependencies.append("pyftpdlib")
        if normalized_protocol == "ftps":
            try:
                import OpenSSL  # noqa: F401
            except ImportError:
                missing_dependencies.append("pyOpenSSL")
        if normalized_protocol == "sftp" and paramiko is None:
            missing_dependencies.append("paramiko")

        if missing_dependencies:
            dependency_text = ", ".join(missing_dependencies)
            return OperationResult(
                False,
                (
                    f"{environment}: {normalized_protocol.upper()} 서버를 사용하려면 requirements.txt 설치가 필요합니다."
                    if not is_packaged_runtime()
                    else f"{environment}: {normalized_protocol.upper()} 서버 구성요소가 누락되었거나 손상되었을 수 있습니다."
                ),
                details=self._dependency_resolution_message(
                    feature_name=f"{normalized_protocol.upper()} 서버",
                    dependency_names=missing_dependencies,
                ),
                payload={"missing_dependencies": missing_dependencies, "dependency_text": dependency_text},
            )

        return OperationResult(
            True,
            f"{environment}: {normalized_protocol.upper()} 서버 사용 준비가 완료되었습니다.",
            details="필수 런타임 의존성이 확인되었습니다.",
        )

    def run_temporary_server(
        self,
        protocol: str,
        bind_host: str,
        port: int | str | None,
        root_folder: str,
        username: str,
        password: str,
        read_only: bool,
        anonymous_readonly: bool,
        cancel_event=None,
        progress_callback=None,
    ) -> OperationResult:
        normalized_protocol = validate_ftp_protocol(protocol)
        actual_bind_host = validate_optional_ipv4(bind_host, "바인드 IP") or "0.0.0.0"
        validated_port = parse_positive_int(
            port if port not in (None, "") else default_ftp_port(normalized_protocol, server_mode=True),
            "FTP 서버 포트",
            minimum=1,
            maximum=65535,
        )
        validated_root = validate_existing_directory(root_folder, "공유 루트 폴더")
        validated_username = validate_ftp_username(username, "sftp" if normalized_protocol == "sftp" else "ftp")
        validated_password = require_text(password, "비밀번호")
        if anonymous_readonly and not read_only:
            raise ValueError("익명 접속은 읽기 전용일 때만 사용할 수 있습니다.")
        if normalized_protocol == "sftp":
            anonymous_readonly = False

        runtime = FtpServerRuntime(
            protocol=normalized_protocol,
            bind_host=self._display_host(actual_bind_host),
            port=validated_port,
            root_folder=validated_root,
            username=validated_username,
            read_only=bool(read_only),
            anonymous_readonly=bool(anonymous_readonly),
        )

        if normalized_protocol == "ftps":
            cert_file, key_file = self.ensure_ftps_certificate()
            runtime.certificate_fingerprint = self._certificate_fingerprint(cert_file)
            return self._run_ftp_family_server(
                runtime,
                actual_bind_host=actual_bind_host,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
                use_tls=True,
                cert_file=cert_file,
                key_file=key_file,
                password=validated_password,
            )
        if normalized_protocol == "ftp":
            return self._run_ftp_family_server(
                runtime,
                actual_bind_host=actual_bind_host,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
                use_tls=False,
                cert_file=None,
                key_file=None,
                password=validated_password,
            )

        self._ensure_sftp_runtime_support()
        host_key_file = self.ensure_sftp_host_key()
        runtime.host_key_fingerprint = self._host_key_fingerprint(host_key_file)
        return self._run_sftp_server(
            runtime,
            actual_bind_host=actual_bind_host,
            password=validated_password,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    def ensure_ftps_certificate(self) -> tuple[Path, Path]:
        self.paths.ftp_keys_dir.mkdir(parents=True, exist_ok=True)
        cert_path = self.paths.ftp_keys_dir / "ftps-cert.pem"
        key_path = self.paths.ftp_keys_dir / "ftps-key.pem"
        if cert_path.exists() and key_path.exists():
            return cert_path, key_path

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "KR"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NetOps Suite"),
                x509.NameAttribute(NameOID.COMMON_NAME, "NetOps Suite Local FTPS"),
            ]
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow() - timedelta(days=1))
            .not_valid_after(datetime.utcnow() + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return cert_path, key_path

    def ensure_sftp_host_key(self) -> Path:
        self.paths.ftp_keys_dir.mkdir(parents=True, exist_ok=True)
        key_path = self.paths.ftp_keys_dir / "sftp-host.key"
        if not key_path.exists():
            key = paramiko.RSAKey.generate(2048)
            key.write_private_key_file(str(key_path))
        return key_path

    def _run_ftp_family_server(
        self,
        runtime: FtpServerRuntime,
        *,
        actual_bind_host: str,
        cancel_event,
        progress_callback,
        use_tls: bool,
        cert_file: Path | None,
        key_file: Path | None,
        password: str,
    ) -> OperationResult:
        self._ensure_pyftpdlib_support()
        if use_tls:
            self._ensure_ftps_runtime_support()

        authorizer = DummyAuthorizer()
        permissions = "elradfmwMT" if not runtime.read_only else "elr"
        authorizer.add_user(runtime.username, password, runtime.root_folder, perm=permissions)
        if runtime.anonymous_readonly:
            authorizer.add_anonymous(runtime.root_folder, perm="elr")

        session_counter = {"count": 0}
        counter_lock = threading.Lock()

        def emit_log(text: str) -> None:
            self.logger.info(text)
            if progress_callback is not None:
                progress_callback.emit({"kind": "server_log", "message": text})

        def emit_runtime() -> None:
            if progress_callback is not None:
                progress_callback.emit(
                    {
                        "kind": "server_runtime",
                        "runtime": replace(runtime, session_count=session_counter["count"]),
                    }
                )

        base_handler = TLS_FTPHandler if use_tls else FTPHandler

        class LoggingHandler(base_handler):
            banner = "NetOps Suite temporary FTP server ready."

            def on_connect(self):
                with counter_lock:
                    session_counter["count"] += 1
                emit_log(f"[접속] {self.remote_ip}:{self.remote_port}")
                emit_runtime()

            def on_disconnect(self):
                with counter_lock:
                    session_counter["count"] = max(0, session_counter["count"] - 1)
                emit_log(f"[연결 종료] {self.remote_ip}:{self.remote_port}")
                emit_runtime()

            def on_login(self, username):
                emit_log(f"[로그인] {username}")

            def on_login_failed(self, username, password):
                emit_log(f"[로그인 실패] {username}")

            def on_file_sent(self, file):
                emit_log(f"[다운로드 완료] {file}")

            def on_file_received(self, file):
                emit_log(f"[업로드 완료] {file}")

            def on_incomplete_file_received(self, file):
                emit_log(f"[업로드 중단] {file}")

        LoggingHandler.authorizer = authorizer
        if use_tls:
            LoggingHandler.certfile = str(cert_file)
            LoggingHandler.keyfile = str(key_file)
            LoggingHandler.tls_control_required = True
            LoggingHandler.tls_data_required = True

        try:
            server = ThreadedFTPServer((actual_bind_host, runtime.port), LoggingHandler)
        except OSError as exc:
            raise RuntimeError(self._friendly_bind_error(exc, runtime.port)) from exc

        server_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"timeout": 0.2, "blocking": True, "handle_exit": False},
            daemon=True,
        )
        server_thread.start()

        emit_log(f"[서버 시작] {runtime.protocol.upper()} {runtime.bind_host}:{runtime.port} / 루트 {runtime.root_folder}")
        emit_runtime()
        while cancel_event is None or not cancel_event.is_set():
            if not server_thread.is_alive():
                break
            time.sleep(0.2)

        server.close_all()
        server_thread.join(timeout=3)
        emit_log(f"[서버 중지] {runtime.protocol.upper()} 서버를 종료했습니다.")
        return OperationResult(True, f"{runtime.protocol.upper()} 임시 서버를 중지했습니다.")

    def _run_sftp_server(
        self,
        runtime: FtpServerRuntime,
        *,
        actual_bind_host: str,
        password: str,
        cancel_event,
        progress_callback,
    ) -> OperationResult:
        host_key_path = self.ensure_sftp_host_key()
        host_key = paramiko.RSAKey.from_private_key_file(str(host_key_path))

        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((actual_bind_host, runtime.port))
            listener.listen(25)
            listener.settimeout(0.5)
        except OSError as exc:
            raise RuntimeError(self._friendly_bind_error(exc, runtime.port)) from exc

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
                    {
                        "kind": "server_runtime",
                        "runtime": replace(runtime, session_count=session_counter["count"]),
                    }
                )

        def handle_client(client_socket: socket.socket, client_address: tuple[str, int]) -> None:
            transport = paramiko.Transport(client_socket)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler(
                "sftp",
                paramiko.SFTPServer,
                _RootedSFTPServer,
                root_dir=runtime.root_folder,
                read_only=runtime.read_only,
                log_callback=emit_log,
            )
            server_interface = _SFTPAuthServer(runtime.username, password, emit_log)

            with counter_lock:
                session_counter["count"] += 1
            emit_log(f"[접속] {client_address[0]}:{client_address[1]}")
            emit_runtime()

            try:
                transport.start_server(server=server_interface)
                while transport.is_active() and (cancel_event is None or not cancel_event.is_set()):
                    time.sleep(0.2)
            except Exception as exc:
                emit_log(f"[SFTP 오류] {client_address[0]}:{client_address[1]} / {exc}")
            finally:
                transport.close()
                client_socket.close()
                with counter_lock:
                    session_counter["count"] = max(0, session_counter["count"] - 1)
                emit_log(f"[연결 종료] {client_address[0]}:{client_address[1]}")
                emit_runtime()

        emit_log(f"[서버 시작] SFTP {runtime.bind_host}:{runtime.port} / 루트 {runtime.root_folder}")
        emit_runtime()

        try:
            while cancel_event is None or not cancel_event.is_set():
                try:
                    client_socket, client_address = listener.accept()
                except socket.timeout:
                    continue
                thread = threading.Thread(target=handle_client, args=(client_socket, client_address), daemon=True)
                thread.start()
                worker_threads.append(thread)
        finally:
            listener.close()
            for thread in worker_threads:
                thread.join(timeout=1.5)
            emit_log("[서버 중지] SFTP 서버를 종료했습니다.")

        return OperationResult(True, "SFTP 임시 서버를 중지했습니다.")

    def _ensure_ftps_runtime_support(self) -> None:
        try:
            import OpenSSL  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("FTPS 서버를 사용하려면 pyOpenSSL 설치가 필요합니다.") from exc

    def _ensure_pyftpdlib_support(self) -> None:
        if DummyAuthorizer is None or ThreadedFTPServer is None:
            raise RuntimeError("FTP/FTPS 서버 기능을 사용하려면 pyftpdlib 설치가 필요합니다.")

    def _ensure_sftp_runtime_support(self) -> None:
        if paramiko is None:
            raise RuntimeError("SFTP 서버 기능을 사용하려면 paramiko 설치가 필요합니다.")

    def _ensure_ftps_runtime_support(self) -> None:
        try:
            import OpenSSL  # noqa: F401
        except ImportError:
            support = self.runtime_support_status("ftps")
            raise RuntimeError("\n\n".join(part for part in (support.message, support.details) if part))

    def _ensure_pyftpdlib_support(self) -> None:
        if DummyAuthorizer is None or ThreadedFTPServer is None:
            support = self.runtime_support_status("ftp")
            raise RuntimeError("\n\n".join(part for part in (support.message, support.details) if part))

    def _ensure_sftp_runtime_support(self) -> None:
        if paramiko is None:
            support = self.runtime_support_status("sftp")
            raise RuntimeError("\n\n".join(part for part in (support.message, support.details) if part))

    def _dependency_resolution_message(self, feature_name: str, dependency_names: list[str]) -> str:
        dependency_text = ", ".join(dependency_names)
        if is_packaged_runtime():
            return (
                f"{feature_name} 배포 구성요소({dependency_text})를 불러오지 못했습니다.\n"
                "설치본이 손상되었거나 배포에 누락이 있을 수 있습니다.\n"
                "프로그램을 다시 설치하거나 최신 설치본으로 업데이트해 주세요."
            )
        return (
            f"{feature_name} 개발 의존성({dependency_text})이 현재 환경에 없습니다.\n"
            "프로젝트 루트에서 `pip install -r requirements.txt`를 실행한 뒤 다시 시도해 주세요."
        )

    def _friendly_bind_error(self, exc: OSError, port: int) -> str:
        if exc.errno in {errno.EADDRINUSE, 10048}:
            return f"포트 {port}는 이미 사용 중입니다."
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

    def _certificate_fingerprint(self, cert_path: Path) -> str:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        digest = hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
        return "SHA256:" + digest

    def _host_key_fingerprint(self, key_path: Path) -> str:
        key = paramiko.RSAKey.from_private_key_file(str(key_path))
        digest = hashlib.sha256(key.asbytes()).hexdigest().upper()
        return "SHA256:" + digest
