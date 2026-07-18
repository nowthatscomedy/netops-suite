from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from qa.offscreen.harness import OffscreenQaHarness


@pytest.fixture
def export_harness(qapp, tmp_path: Path):
    del qapp
    project_root = Path(__file__).resolve().parents[1]
    harness = OffscreenQaHarness(
        project_root=project_root,
        config_path=project_root / "qa" / "offscreen" / "scenarios.json",
        output_dir=tmp_path / "evidence",
    )
    harness._setup()
    try:
        yield harness
    finally:
        harness._teardown()


def _fill_table(table: QTableWidget, first_values: tuple[str, ...] = ()) -> None:
    table.setRowCount(1)
    for column in range(table.columnCount()):
        value = (
            first_values[column]
            if column < len(first_values)
            else f"value-{column}"
        )
        table.setItem(0, column, QTableWidgetItem(value))


def _prepare_export_actions(tab) -> list[tuple[str, str, str, Callable[[], None]]]:
    _fill_table(tab.ping_table, ("Gateway", "192.0.2.1"))
    tab.ping_log_lines = {
        ("Gateway", "192.0.2.1"): ["PING 192.0.2.1", "Reply from 192.0.2.1"]
    }
    tab.ping_table.selectRow(0)

    _fill_table(tab.tcp_table, ("Web", "192.0.2.10", "443"))
    tab.tcp_log_lines = {
        ("Web", "192.0.2.10", 443): ["TCP 192.0.2.10:443", "Connected"]
    }
    tab.tcp_table.selectRow(0)

    tab.dns_output.setPlainText("Name: example.test\nAddress: 192.0.2.53")
    tab.dns_export_button.setEnabled(True)

    for table in (
        tab.ftp_transfer_table,
        tab.scp_transfer_table,
        tab.tftp_transfer_table,
    ):
        _fill_table(table)

    tab._ftp_client_logs = ["FTP client log"]
    tab._ftp_server_logs = ["FTP server log"]
    tab._scp_client_logs = ["SCP client log"]
    tab._scp_server_logs = ["SCP server log"]
    tab._tftp_client_logs = ["TFTP client log"]
    tab._tftp_server_logs = ["TFTP server log"]
    for button in (
        tab.ftp_transfer_export_button,
        tab.ftp_client_log_export_button,
        tab.ftp_server_log_export_button,
        tab.scp_transfer_export_button,
        tab.scp_client_log_export_button,
        tab.scp_server_log_export_button,
        tab.tftp_transfer_export_button,
        tab.tftp_client_log_export_button,
        tab.tftp_server_log_export_button,
    ):
        button.setEnabled(True)

    return [
        ("ping_results", "csv", "Gateway", tab.ping_csv_button.click),
        ("ping_logs", "log", "[Gateway | 192.0.2.1]", tab.ping_log_export_button.click),
        ("tcp_results", "csv", "Web", tab.tcp_csv_button.click),
        (
            "tcp_logs",
            "log",
            "[Web | 192.0.2.10:443]",
            tab.tcp_log_export_button.click,
        ),
        ("dns_lookup", "txt", "example.test", tab.dns_export_button.click),
        ("ftp_transfers", "csv", "value-0", tab.ftp_transfer_export_button.click),
        (
            "ftp_client_log",
            "txt",
            "FTP client log",
            tab.ftp_client_log_export_button.click,
        ),
        (
            "ftp_server_log",
            "txt",
            "FTP server log",
            tab.ftp_server_log_export_button.click,
        ),
        ("scp_transfers", "csv", "value-0", tab.scp_transfer_export_button.click),
        (
            "scp_client_log",
            "txt",
            "SCP client log",
            tab.scp_client_log_export_button.click,
        ),
        (
            "scp_server_log",
            "txt",
            "SCP server log",
            tab.scp_server_log_export_button.click,
        ),
        (
            "tftp_transfers",
            "csv",
            "value-0",
            tab.tftp_transfer_export_button.click,
        ),
        (
            "tftp_client_log",
            "txt",
            "TFTP client log",
            tab.tftp_client_log_export_button.click,
        ),
        (
            "tftp_server_log",
            "txt",
            "TFTP server log",
            tab.tftp_server_log_export_button.click,
        ),
    ]


def test_all_explicit_diagnostic_exports_prompt_and_use_selected_filename(
    export_harness: OffscreenQaHarness,
    tmp_path: Path,
):
    harness = export_harness
    window = harness._require_window()
    tab = window.diagnostics_tab
    actions = _prepare_export_actions(tab)
    calls: list[tuple[str, str, str]] = []
    selected_paths = [
        tmp_path / f"chosen-{index:02d}.wrong"
        for index in range(len(actions))
    ]

    def choose(caption: str, suggested_path: str, file_filter: str):
        calls.append((caption, suggested_path, file_filter))
        return str(selected_paths[len(calls) - 1]), file_filter

    tab._get_export_save_file_name = choose
    for _prefix, _extension, _expected_text, invoke in actions:
        invoke()

    assert len(calls) == len(actions)
    for index, (prefix, extension, expected_text, _invoke) in enumerate(actions):
        _caption, suggested_path, file_filter = calls[index]
        suggestion = Path(suggested_path)
        assert suggestion.parent == harness.state.paths.exports_dir
        assert suggestion.name.startswith(f"{prefix}_")
        assert suggestion.suffix == f".{extension}"
        assert f"*.{extension}" in file_filter

        corrected_path = selected_paths[index].with_suffix(f".{extension}")
        assert corrected_path.is_file()
        assert not selected_paths[index].exists()
        encoding = "utf-8-sig" if extension == "csv" else "utf-8"
        assert expected_text in corrected_path.read_text(encoding=encoding)


def test_all_explicit_diagnostic_export_cancellations_create_nothing(
    export_harness: OffscreenQaHarness,
):
    harness = export_harness
    window = harness._require_window()
    tab = window.diagnostics_tab
    actions = _prepare_export_actions(tab)
    export_root = harness.state.paths.exports_dir
    before = {path.relative_to(export_root) for path in export_root.rglob("*")}
    messages_before = list(harness._message_log)
    calls: list[tuple[str, str, str]] = []

    def cancel(caption: str, suggested_path: str, file_filter: str):
        calls.append((caption, suggested_path, file_filter))
        return "", ""

    tab._get_export_save_file_name = cancel
    for _prefix, _extension, _expected_text, invoke in actions:
        invoke()

    after = {path.relative_to(export_root) for path in export_root.rglob("*")}
    assert len(calls) == len(actions)
    assert after == before
    assert harness._message_log == messages_before


def test_export_write_failures_are_reported_without_creating_files(
    export_harness: OffscreenQaHarness,
    tmp_path: Path,
):
    harness = export_harness
    window = harness._require_window()
    tab = window.diagnostics_tab
    _prepare_export_actions(tab)
    missing_parent = tmp_path / "missing-parent"

    def choose(caption: str, suggested_path: str, file_filter: str):
        del suggested_path
        extension = "csv" if "*.csv" in file_filter else "txt"
        return str(missing_parent / f"{caption}.{extension}"), file_filter

    tab._get_export_save_file_name = choose
    tab.ping_csv_button.click()
    tab.ftp_client_log_export_button.click()
    tab.dns_export_button.click()

    assert not missing_parent.exists()
    assert any(
        kind == "warning" and "저장 실패" in title
        for kind, title, _text in harness._message_log
    )
    assert "저장하지 못했습니다" in tab.dns_status_label.text()
