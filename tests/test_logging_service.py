from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import qDebug, qWarning

from app.assistant import (
    AuditLogger,
    PermissionClass,
    ToolCallRequest,
    ToolDescriptor,
    ToolRegistry,
)
from app.assistant.executor import ToolExecutor
from app.services.logging_service import configure_logging, shutdown_logging


def _read_rotated_logs(log_path: Path) -> str:
    parts = []
    for path in sorted(log_path.parent.glob(f"{log_path.name}*"), reverse=True):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_application_log_has_runtime_source_and_redacts_credentials(tmp_path):
    log_path = tmp_path / "logs" / "app.log"
    callback_lines: list[str] = []
    logger = configure_logging(log_path, callback_lines.append)
    try:
        component = logging.getLogger("netops_suite.diagnostics")
        component.error(
            (
                "request failed password=plain-secret "
                "authorization: Bearer abc.def.ghi "
                "url=https://alice:p455@example.test "
                "token=sk-abcdefghijklmnopqrstuv "
                "jwt=eyJabcdefghijk.abcdefghijkl.abcdefghijkl "
                '"private_key": "line one line two"'
            )
        )
    finally:
        shutdown_logging(logger)

    content = log_path.read_text(encoding="utf-8")
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", content)
    assert "| ERROR    | netops_suite.diagnostics | MainThread |" in content
    assert "app_version=" in content
    assert "pid=" in content
    assert "python=" in content
    assert "pyside=" in content
    assert "qt=" in content
    assert "qt_platform=" in content
    assert "os=" in content
    assert "architecture=" in content
    assert "admin=" in content
    assert re.search(r"session_id=[0-9a-f]{12}\b", content)
    assert "rotation_max_bytes=" in content
    assert "rotation_backups=" in content
    for secret in (
        "plain-secret",
        "abc.def.ghi",
        "p455",
        "sk-abcdefghijklmnopqrstuv",
        "eyJabcdefghijk.abcdefghijkl.abcdefghijkl",
        "line one line two",
    ):
        assert secret not in content
        assert all(secret not in line for line in callback_lines)
    assert "[redacted]" in content


def test_assistant_audit_jsonl_uses_common_token_and_url_redaction(tmp_path):
    audit_path = tmp_path / "netops_assistant_audit.jsonl"
    audit = AuditLogger(audit_path)

    audit.log_event(
        "tool_result",
        {
            "message": (
                "provider returned sk-releasecandidate1234567890 and "
                "eyJabcdefghijk.abcdefghijkl.abcdefghijkl"
            ),
            "url": "https://alice:supersecret@example.test/private",
        },
    )

    content = audit_path.read_text(encoding="utf-8")
    for secret in (
        "sk-releasecandidate1234567890",
        "eyJabcdefghijk.abcdefghijkl.abcdefghijkl",
        "alice",
        "supersecret",
    ):
        assert secret not in content
    assert "[redacted]" in content


def test_assistant_tool_failure_logs_traceback_without_exposing_error_to_result(tmp_path):
    log_path = tmp_path / "app.log"
    app_logger = configure_logging(log_path)
    registry = ToolRegistry()
    registry.register(
        ToolDescriptor(
            name="qa_failure",
            permission_class=PermissionClass.READ_LOCAL,
        ),
        lambda _state, _arguments: (_ for _ in ()).throw(
            RuntimeError("provider failed token=sk-releasecandidate1234567890")
        ),
    )

    try:
        _decision, result = ToolExecutor(object(), registry).execute(
            ToolCallRequest(tool_name="qa_failure")
        )
    finally:
        shutdown_logging(app_logger)

    assert result is not None
    assert result.success is False
    assert "sk-releasecandidate" not in result.error
    content = log_path.read_text(encoding="utf-8")
    assert "Assistant tool execution failed. tool=qa_failure" in content
    assert "Traceback (most recent call last):" in content
    assert "sk-releasecandidate1234567890" not in content
    assert "token=[redacted]" in content


def test_uncaught_main_and_thread_exceptions_include_tracebacks_and_restore_hooks(
    tmp_path,
    monkeypatch,
):
    prior_main_calls = []
    prior_thread_calls = []

    def prior_main_hook(*args):
        prior_main_calls.append(args)

    def prior_thread_hook(args):
        prior_thread_calls.append(args)

    monkeypatch.setattr(sys, "excepthook", prior_main_hook)
    monkeypatch.setattr(threading, "excepthook", prior_thread_hook)
    log_path = tmp_path / "app.log"
    logger = configure_logging(log_path)
    installed_main_hook = sys.excepthook
    installed_thread_hook = threading.excepthook
    assert installed_main_hook is not prior_main_hook
    assert installed_thread_hook is not prior_thread_hook

    try:
        try:
            raise RuntimeError("main failure password=main-secret")
        except RuntimeError:
            installed_main_hook(*sys.exc_info())

        try:
            raise ValueError("worker failure token=worker-secret")
        except ValueError:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            installed_thread_hook(
                SimpleNamespace(
                    exc_type=exc_type,
                    exc_value=exc_value,
                    exc_traceback=exc_traceback,
                    thread=SimpleNamespace(name="inventory-worker"),
                )
            )
    finally:
        shutdown_logging(logger)

    assert sys.excepthook is prior_main_hook
    assert threading.excepthook is prior_thread_hook
    assert len(prior_main_calls) == 1
    assert len(prior_thread_calls) == 1
    content = log_path.read_text(encoding="utf-8")
    assert "Unhandled exception on the main thread." in content
    assert "RuntimeError: main failure password=[redacted]" in content
    assert "Unhandled exception on worker thread 'inventory-worker'." in content
    assert "ValueError: worker failure token=[redacted]" in content
    assert "main-secret" not in content
    assert "worker-secret" not in content


def test_qt_warning_is_logged_but_qt_debug_noise_is_ignored(tmp_path):
    log_path = tmp_path / "app.log"
    logger = configure_logging(log_path)
    try:
        qDebug("routine qt debug output")
        qWarning("actionable qt warning")
    finally:
        shutdown_logging(logger)

    content = log_path.read_text(encoding="utf-8")
    assert "actionable qt warning" in content
    assert "Qt message" in content
    assert "routine qt debug output" not in content


def test_error_logged_inside_exception_handler_gets_active_traceback(tmp_path):
    log_path = tmp_path / "app.log"
    logger = configure_logging(log_path)
    try:
        try:
            raise LookupError("profile cache is corrupt")
        except LookupError:
            logging.getLogger("netops_suite.profile").error("Profile cache load failed.")
    finally:
        shutdown_logging(logger)

    content = log_path.read_text(encoding="utf-8")
    assert "Profile cache load failed." in content
    assert "Traceback (most recent call last):" in content
    assert "LookupError: profile cache is corrupt" in content


def test_application_log_rotation_is_bounded(tmp_path):
    log_path = tmp_path / "app.log"
    logger = configure_logging(log_path, max_bytes=1024, backup_count=2)
    try:
        for index in range(40):
            logger.error("diagnostic event %02d %s", index, "x" * 220)
    finally:
        shutdown_logging(logger)

    log_files = sorted(tmp_path.glob("app.log*"))
    assert log_path in log_files
    assert tmp_path / "app.log.1" in log_files
    assert len(log_files) == 3
    content = _read_rotated_logs(log_path)
    assert "diagnostic event 39" in content
    assert "diagnostic event 00" not in content


def test_shutdown_releases_logger_namespace_for_later_consumers(tmp_path):
    logger = configure_logging(tmp_path / "app.log")

    shutdown_logging(logger)

    assert logger.handlers == []
    assert logger.propagate is True
