from __future__ import annotations

import logging
import os
import platform
import re
import sys
import threading
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Callable

from app.version import __version__


DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
REDACTED = "[redacted]"

_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (
        ["']?
        (?:api[_-]?key|authorization|bearer|cookie|credential|password|passwd|
           passphrase|private[_-]?key|secret|session(?:[_-]?(?:key|token))?|token)
        ["']?
        \s*[:=]\s*
    )
    (?:
        "[^"\r\n]*"
        |
        '[^'\r\n]*'
        |
        [^\s,;&}\]]+
    )
    """,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_URL_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@",
    re.IGNORECASE,
)
_TOKEN_SHAPE_RE = re.compile(
    r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)

_hook_lock = threading.RLock()
_hook_logger: logging.Logger | None = None
_previous_sys_excepthook = None
_previous_threading_excepthook = None
_previous_qt_message_handler = None
_qt_hook_available = False


def redact_log_text(value: str) -> str:
    """Remove common credential shapes from a message before it reaches a log sink."""
    redacted = _URL_CREDENTIAL_RE.sub(r"\g<scheme>[redacted]@", str(value))
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
    redacted = _ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{REDACTED}",
        redacted,
    )
    redacted = _TOKEN_SHAPE_RE.sub(REDACTED, redacted)
    return _JWT_RE.sub(REDACTED, redacted)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


class ActiveExceptionFilter(logging.Filter):
    """Attach the current traceback when code logs an error inside an except block."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR and not record.exc_info:
            exc_info = sys.exc_info()
            if exc_info[0] is not None:
                record.exc_info = exc_info
        return True


class CallbackLogHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:
            # A UI callback must never break file logging or the calling operation.
            pass


def _flush_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        try:
            handler.flush()
        except Exception:
            pass


def _call_previous_sys_hook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    previous = _previous_sys_excepthook
    if previous is not None and previous is not _sys_exception_hook:
        previous(exc_type, exc_value, exc_traceback)


def _sys_exception_hook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        _call_previous_sys_hook(exc_type, exc_value, exc_traceback)
        return

    logger = _hook_logger
    if logger is not None:
        logger.critical(
            "Unhandled exception on the main thread.",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        _flush_logger(logger)
    _call_previous_sys_hook(exc_type, exc_value, exc_traceback)


def _thread_exception_hook(args: threading.ExceptHookArgs) -> None:
    logger = _hook_logger
    if logger is not None and not issubclass(args.exc_type, (KeyboardInterrupt, SystemExit)):
        thread_name = getattr(args.thread, "name", None) or "unknown"
        logger.critical(
            "Unhandled exception on worker thread %r.",
            thread_name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        _flush_logger(logger)

    previous = _previous_threading_excepthook
    if previous is not None and previous is not _thread_exception_hook:
        previous(args)


def _qt_message_hook(message_type, context, message: str) -> None:
    logger = _hook_logger
    if logger is not None:
        try:
            from PySide6.QtCore import QtMsgType

            level = {
                QtMsgType.QtWarningMsg: logging.WARNING,
                QtMsgType.QtCriticalMsg: logging.ERROR,
                QtMsgType.QtFatalMsg: logging.CRITICAL,
            }.get(message_type)
            if level is not None:
                file_name = Path(str(getattr(context, "file", "") or "")).name
                line = int(getattr(context, "line", 0) or 0)
                function = str(getattr(context, "function", "") or "")
                category = str(getattr(context, "category", "") or "qt")
                location = ":".join(
                    part
                    for part in (
                        f"{file_name}:{line}" if file_name else "",
                        function,
                    )
                    if part
                )
                logger.log(
                    level,
                    "Qt message [%s]%s: %s",
                    category,
                    f" ({location})" if location else "",
                    message,
                )
                if level >= logging.CRITICAL:
                    _flush_logger(logger)
        except Exception:
            # Qt may emit messages during interpreter teardown; diagnostics must
            # not introduce a second failure.
            pass

    previous = _previous_qt_message_handler
    if previous is not None and previous is not _qt_message_hook:
        try:
            previous(message_type, context, message)
        except Exception:
            pass


def install_diagnostic_hooks(logger: logging.Logger) -> None:
    """Capture uncaught Python/thread/Qt failures in the active application log."""
    global _hook_logger
    global _previous_qt_message_handler
    global _previous_sys_excepthook
    global _previous_threading_excepthook
    global _qt_hook_available

    with _hook_lock:
        _hook_logger = logger
        if sys.excepthook is not _sys_exception_hook:
            _previous_sys_excepthook = sys.excepthook
            sys.excepthook = _sys_exception_hook
        if hasattr(threading, "excepthook") and threading.excepthook is not _thread_exception_hook:
            _previous_threading_excepthook = threading.excepthook
            threading.excepthook = _thread_exception_hook

        try:
            from PySide6.QtCore import qInstallMessageHandler

            current_previous = qInstallMessageHandler(_qt_message_hook)
            if current_previous is not _qt_message_hook:
                _previous_qt_message_handler = current_previous
            _qt_hook_available = True
        except (ImportError, RuntimeError):
            _qt_hook_available = False


def uninstall_diagnostic_hooks(logger: logging.Logger | None = None) -> None:
    global _hook_logger
    global _previous_qt_message_handler
    global _previous_sys_excepthook
    global _previous_threading_excepthook
    global _qt_hook_available

    with _hook_lock:
        if logger is not None and _hook_logger is not logger:
            return
        if sys.excepthook is _sys_exception_hook and _previous_sys_excepthook is not None:
            sys.excepthook = _previous_sys_excepthook
        if (
            hasattr(threading, "excepthook")
            and threading.excepthook is _thread_exception_hook
            and _previous_threading_excepthook is not None
        ):
            threading.excepthook = _previous_threading_excepthook
        if _qt_hook_available:
            try:
                from PySide6.QtCore import qInstallMessageHandler

                qInstallMessageHandler(_previous_qt_message_handler)
            except (ImportError, RuntimeError):
                pass

        _hook_logger = None
        _previous_sys_excepthook = None
        _previous_threading_excepthook = None
        _previous_qt_message_handler = None
        _qt_hook_available = False


def _runtime_metadata() -> dict[str, str]:
    try:
        import PySide6
        from PySide6.QtCore import qVersion
        from PySide6.QtGui import QGuiApplication

        pyside_version = str(PySide6.__version__)
        qt_version = str(qVersion())
        qt_platform = str(QGuiApplication.platformName() or "unavailable")
    except (ImportError, RuntimeError):
        pyside_version = "unavailable"
        qt_version = "unavailable"
        qt_platform = "unavailable"

    try:
        from app.utils.admin import is_running_as_admin

        admin = str(is_running_as_admin()).lower()
    except (ImportError, RuntimeError):
        admin = "unknown"

    return {
        "app_version": __version__,
        "pid": str(os.getpid()),
        "python": platform.python_version(),
        "python_impl": platform.python_implementation(),
        "pyside": pyside_version,
        "qt": qt_version,
        "qt_platform": qt_platform,
        "os": platform.system() or "unknown",
        "os_release": platform.release() or "unknown",
        "architecture": platform.machine() or "unknown",
        "admin": admin,
        "packaged": str(bool(getattr(sys, "frozen", False))).lower(),
    }


def configure_logging(
    log_path: Path,
    callback: Callable[[str], None] | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    logger = logging.getLogger("netops_suite")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = RedactingFormatter(
        (
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | "
            "%(threadName)s | %(module)s:%(lineno)d | %(message)s"
        ),
        "%Y-%m-%d %H:%M:%S",
    )

    if callback:
        callback_handler = CallbackLogHandler(callback)
        callback_handler.setFormatter(formatter)
        callback_handler.addFilter(ActiveExceptionFilter())
        logger.addHandler(callback_handler)

    file_logging_ready = False
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max(1024, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ActiveExceptionFilter())
        logger.addHandler(file_handler)
        file_logging_ready = True
    except OSError as exc:
        logger.warning("File logging unavailable at %s: %s", log_path, exc)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    install_diagnostic_hooks(logger)

    metadata = _runtime_metadata()
    session_id = uuid.uuid4().hex[:12]
    if file_logging_ready:
        logger.info(
            (
                "Application session started. session_id=%s app_version=%s pid=%s python=%s "
                "python_impl=%s pyside=%s qt=%s qt_platform=%s os=%s os_release=%s "
                "architecture=%s admin=%s packaged=%s log_path=%s rotation_max_bytes=%s "
                "rotation_backups=%s"
            ),
            session_id,
            metadata["app_version"],
            metadata["pid"],
            metadata["python"],
            metadata["python_impl"],
            metadata["pyside"],
            metadata["qt"],
            metadata["qt_platform"],
            metadata["os"],
            metadata["os_release"],
            metadata["architecture"],
            metadata["admin"],
            metadata["packaged"],
            log_path,
            max(1024, int(max_bytes)),
            max(1, int(backup_count)),
        )
    else:
        logger.info(
            "Application session started without file output. session_id=%s app_version=%s",
            session_id,
            metadata["app_version"],
        )
    return logger


def shutdown_logging(logger: logging.Logger | None = None) -> None:
    target = logger or logging.getLogger("netops_suite")
    if target.handlers:
        target.info("Application session ended.")
        _flush_logger(target)
    uninstall_diagnostic_hooks(target)
    for handler in list(target.handlers):
        target.removeHandler(handler)
        handler.close()
    # Leave the logger namespace usable for tests, embedding, or a later
    # reinitialization in the same process. configure_logging() disables
    # propagation again while the application owns its handlers.
    target.propagate = True
