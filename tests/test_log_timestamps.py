from __future__ import annotations

import re
from datetime import datetime

import pytest

from app.models.result_models import OperationResult
from app.ui.tabs.diagnostics_tab import DiagnosticsTab
from app.utils.log_format import timestamp_lines
from tests.test_diagnostics_state import build_fake_state

STAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


def test_timestamp_lines_prefixes_each_non_blank_line():
    now = datetime(2026, 10, 15, 9, 5, 7)

    assert timestamp_lines("[접속] 100.127.48.126:36438", now) == "[09:05:07] [접속] 100.127.48.126:36438"
    assert timestamp_lines("\n[결과] 완료\r\n세부", now) == "\n[09:05:07] [결과] 완료\n[09:05:07] 세부"
    assert timestamp_lines("", now) == ""


@pytest.mark.parametrize(
    ("handler", "kind", "logs", "output"),
    [
        ("_handle_ftp_server_progress", "server_log", "_ftp_server_logs", "ftp_server_log_output"),
        ("_handle_ftp_client_progress", "log", "_ftp_client_logs", "ftp_client_log_output"),
        ("_handle_scp_server_progress", "server_log", "_scp_server_logs", "scp_server_log_output"),
        ("_handle_scp_client_progress", "log", "_scp_client_logs", "scp_client_log_output"),
        ("_handle_tftp_server_progress", "server_log", "_tftp_server_logs", "tftp_server_log_output"),
        ("_handle_tftp_client_progress", "log", "_tftp_client_logs", "tftp_client_log_output"),
    ],
)
def test_file_transfer_logs_show_and_save_time(qapp, tmp_path, handler, kind, logs, output):
    tab = DiagnosticsTab(build_fake_state(tmp_path))

    getattr(tab, handler)({"kind": kind, "message": "[SFTP 로그인] netops"})

    saved = getattr(tab, logs)
    assert len(saved) == 1
    assert STAMP.match(saved[0])
    assert saved[0].endswith("[SFTP 로그인] netops")
    assert getattr(tab, output).toPlainText() == saved[0]


def test_trace_output_shows_time_and_still_parses_hops(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab._trace_raw_lines = []
    hop_line = "  1    <1 ms    <1 ms    <1 ms  192.168.0.1"

    tab._handle_trace_progress(hop_line)
    tab._finish_trace("tracert", OperationResult(True, "완료"))

    assert STAMP.match(tab.trace_output.toPlainText())
    assert tab.trace_table.rowCount() == 1


def test_arp_scan_log_shows_time(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab._current_arp_scan_subnet = "192.168.0.0/24"

    tab._finish_arp_scan(OperationResult(True, "스캔 완료", payload=[]))

    assert all(STAMP.match(line) for line in tab.arp_output.toPlainText().splitlines() if line.strip())
