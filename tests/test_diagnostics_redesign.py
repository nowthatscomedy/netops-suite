from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from app.ui.tabs.diagnostics_tab import DiagnosticsTab
from app.ui.tabs.transfer_tab import TransferTab
from test_diagnostics_state import build_fake_state


def test_tool_selection_preserves_inputs_and_does_not_execute(qapp, tmp_path, monkeypatch):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    worker = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    tab.ping_targets_edit.setPlainText("gateway,192.0.2.1")
    tab.tcp_targets_edit.setPlainText("192.0.2.2")
    tab.show()
    for index in range(tab.diagnostic_tool_combo.count()):
        tab.diagnostic_tool_combo.setCurrentIndex(index)
    tab.select_tool("ping")
    qapp.processEvents()
    worker.assert_not_called()
    assert tab.quick_diagnostics_bar.isHidden()
    assert tab.ping_targets_edit.isVisibleTo(tab)
    assert not tab.tcp_targets_edit.isVisibleTo(tab)
    assert not tab.quick_target_edit.isVisibleTo(tab)
    assert tab.ping_targets_edit.toPlainText() == "gateway,192.0.2.1"
    assert tab.tcp_targets_edit.toPlainText() == "192.0.2.2"


def test_collapsed_options_retain_execution_values_and_cancel(qapp, tmp_path, monkeypatch):
    state = build_fake_state(tmp_path)
    state.ping_service.run_multi_ping = Mock()
    tab = DiagnosticsTab(state)
    worker = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    tab.ping_targets_edit.setPlainText("192.0.2.1")
    tab.ping_count_edit.setText("7")
    tab.ping_timeout_edit.setText("1250")
    assert not tab.ping_options_section.isExpanded()
    assert "변경된 옵션 2개" in tab.ping_options_section.toggle_button.text()
    tab.start_ping()
    assert tab.active_task_keys() == {"ping"}
    assert worker.call_args.args[1:] == ("192.0.2.1", 7, 1250)
    tab.start_ping()
    assert worker.call_count == 1
    tab.select_tool("dns")
    tab.select_tool("ping")
    assert not tab.ping_start_button.isEnabled()
    assert tab.ping_cancel_button.isEnabled()
    tab.cancel_ping()
    assert tab.ping_cancel_event.is_set()
    assert tab.active_task_keys() == {"ping"}
    worker.call_args.kwargs["on_finished"]()
    assert tab.ping_start_button.isEnabled()
    assert not tab.ping_cancel_button.isEnabled()
    assert tab.active_task_keys() == set()


@pytest.mark.parametrize("key", ["ping", "tcp", "dns", "trace"])
def test_missing_target_reports_inline_without_starting(qapp, tmp_path, monkeypatch, key):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    worker = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    tab.select_tool(key)
    {
        "ping": tab.start_ping,
        "tcp": tab.start_tcp_check,
        "dns": tab.run_dns_lookup,
        "trace": lambda: tab.start_trace("tracert"),
    }[key]()
    worker.assert_not_called()
    error = getattr(tab, f"{key}_input_error")
    assert error.text().strip()
    assert not error.isHidden()


def test_transfer_workspace_keeps_legacy_state_and_session_owner(qapp, tmp_path, monkeypatch):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    worker = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    tab._ftp_session_id = "existing-session"
    tab.file_transfer_role_combo.setCurrentIndex(1)
    tab.file_transfer_mode_combo.setCurrentIndex(2)
    state = tab.save_ui_state()
    pages_before = tab.diagnostic_stack.count()
    workspace = TransferTab(tab)
    workspace.show()
    qapp.processEvents()
    assert tab.diagnostic_stack.count() == pages_before
    assert tab._ftp_session_id == "existing-session"
    assert tab.save_ui_state()["ftp"] == state["ftp"]
    assert workspace.isAncestorOf(tab.file_transfer_page_stack)
    assert tab.tftp_server_bind_host_edit.isVisibleTo(workspace)
    assert tab.tftp_server_readonly_check.isVisibleTo(workspace)
    assert not tab.tftp_server_log_output.isVisibleTo(workspace)
    worker.assert_not_called()
    requested = []
    tab.transfer_requested.connect(lambda: requested.append(True))
    tab.select_tool("transfer")
    assert requested == [True]
    with pytest.raises(RuntimeError):
        tab.take_transfer_page()


def test_commands_require_explicit_run_and_preserve_dns_confirmation(qapp, tmp_path, monkeypatch):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    handler = Mock()
    monkeypatch.setattr(tab, "_confirm_and_flush_dns_cache", handler)
    tab.select_tool("commands")
    tab.command_tool_combo.setCurrentIndex(tab.command_tool_combo.findData("flush_dns"))
    handler.assert_not_called()
    tab.command_run_button.click()
    handler.assert_called_once()


def test_transfer_profiles_options_and_logs_are_optional(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    assert not tab.ftp_profiles_section.isExpanded()
    assert not tab.scp_profiles_section.isExpanded()
    for protocol in ("ftp", "scp", "tftp"):
        section = getattr(tab, f"{protocol}_options_section")
        timeout = getattr(tab, f"{protocol}_client_timeout_edit")
        original_timeout = timeout.text()
        assert not section.isExpanded()
        section.setExpanded(True)
        section.setExpanded(False)
        assert timeout.text() == original_timeout
        assert not getattr(tab, f"{protocol}_client_log_section").isExpanded()


def test_explicit_numeric_defaults_do_not_count_as_changed(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.ping_count_edit.setText("4")
    tab.ping_timeout_edit.setText("4000")
    assert "변경된" not in tab.ping_options_section.toggle_button.text()
    tab.tcp_count_edit.setText("4")
    tab.tcp_timeout_edit.setText("1000")
    assert "변경된" not in tab.tcp_options_section.toggle_button.text()


def test_running_keys_track_server_and_client_lifecycle(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    assert tab.active_task_keys() == set()
    tab._set_scp_client_busy(True)
    assert tab.is_transfer_running()
    assert tab.active_task_keys() == {"transfer"}
    tab._set_ftp_server_running(True)
    tab._set_scp_client_busy(False)
    assert tab.is_transfer_running()
    tab._set_ftp_server_running(False)
    assert tab.active_task_keys() == set()


@pytest.mark.parametrize("protocol", ["ftp", "scp", "tftp"])
def test_transfer_missing_host_is_inline_and_does_not_run(qapp, tmp_path, monkeypatch, protocol):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    getattr(tab, f"{protocol}_client_host_edit").clear()
    worker = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    {"ftp": tab._connect_ftp_client, "scp": tab._upload_scp_files,
     "tftp": tab._start_tftp_upload}[protocol]()
    worker.assert_not_called()
    assert "호스트" in getattr(tab, f"{protocol}_client_status_label").text()


@pytest.mark.parametrize(
    ("protocol", "defaults"),
    [("ftp", {"port": "21", "timeout": "15"}),
     ("scp", {"port": "22", "timeout": "15"}),
     ("tftp", {"port": "69", "timeout": "5", "retries": "3"})],
)
def test_transfer_default_options_and_custom_values_have_accurate_counts(qapp, tmp_path, protocol, defaults):
    state = build_fake_state(tmp_path)
    runtime = getattr(state, f"{protocol}_runtime")
    runtime["client"] = {
        ("timeout_seconds" if name == "timeout" else name): value
        for name, value in defaults.items()
    }
    tab = DiagnosticsTab(state)
    section = getattr(tab, f"{protocol}_options_section")
    assert "변경된" not in section.toggle_button.text()
    for name, value in defaults.items():
        field = getattr(tab, f"{protocol}_client_{name}_edit")
        field.clear()
        assert "변경된" not in section.toggle_button.text()
        field.setText(value)
        assert "변경된" not in section.toggle_button.text()
    timeout = getattr(tab, f"{protocol}_client_timeout_edit")
    timeout.setText("30")
    assert "변경된 옵션 1개" in section.toggle_button.text()
    timeout.setText(defaults["timeout"])
    assert "변경된" not in section.toggle_button.text()


@pytest.mark.parametrize(
    ("field_name", "value", "options_open"),
    [("port", "70000", False), ("streams", "0", True), ("duration", "invalid", True)],
)
def test_iperf_invalid_numeric_input_is_inline(qapp, tmp_path, monkeypatch, field_name, value, options_open):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.select_tool("iperf")
    tab.show()
    qapp.processEvents()
    tab._iperf_available = True
    monkeypatch.setattr(tab, "refresh_iperf_availability", lambda **_kwargs: None)
    worker = Mock()
    warning = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    field = getattr(tab, f"iperf_{field_name}_edit")
    field.setText(value)
    tab.run_iperf_test()
    qapp.processEvents()
    worker.assert_not_called()
    warning.assert_not_called()
    assert tab.iperf_status_label.text()
    assert qapp.focusWidget() is field
    assert tab.iperf_options_section.isExpanded() is options_open


@pytest.mark.parametrize("public_server", [False, True])
def test_iperf_missing_server_is_inline(qapp, tmp_path, monkeypatch, public_server):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab._iperf_available = True
    monkeypatch.setattr(tab, "refresh_iperf_availability", lambda **_kwargs: None)
    tab.iperf_use_public_server_check.setChecked(public_server)
    worker = Mock()
    warning = Mock()
    monkeypatch.setattr(tab, "_start_worker", worker)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    tab.run_iperf_test()
    worker.assert_not_called()
    warning.assert_not_called()
    assert "서버" in tab.iperf_status_label.text()


@pytest.mark.parametrize(
    ("ip", "prefix", "field_name"),
    [("", "", "ip"), ("wrong", "24", "ip"), ("192.0.2.10", "33", "prefix")],
)
def test_subnet_invalid_input_focuses_field(qapp, tmp_path, ip, prefix, field_name):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.select_tool("subnet")
    tab.show()
    qapp.processEvents()
    tab.subnet_calc_ip_edit.setText(ip)
    tab.subnet_calc_prefix_edit.setText(prefix)
    tab.calculate_subnet_from_tools_inputs()
    assert qapp.focusWidget() is getattr(tab, f"subnet_calc_{field_name}_edit")
    assert "#b42318" in tab.subnet_calc_status_label.styleSheet()
    assert tab.subnet_calc_detail_table.rowCount() == 0


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("subnet", ""), ("subnet", "bad"), ("subnet", "::1/128"),
     ("timeout", "0"), ("workers", "bad")],
)
def test_arp_invalid_input_precedes_execution_confirmation(qapp, tmp_path, monkeypatch, field_name, value):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.select_tool("arp")
    tab.show()
    qapp.processEvents()
    tab.arp_subnet_edit.setText("192.0.2.0/24")
    field = getattr(tab, f"arp_{field_name}_edit")
    field.setText(value)
    confirm = Mock()
    worker = Mock()
    monkeypatch.setattr("app.ui.tabs.diagnostics.tools.confirm_risky_action", confirm)
    monkeypatch.setattr(tab, "_start_worker", worker)
    tab.start_arp_scan()
    confirm.assert_not_called()
    worker.assert_not_called()
    assert tab.arp_input_error.isVisibleTo(tab)
    assert qapp.focusWidget() is field


@pytest.mark.parametrize("tool", ["subnet", "arp"])
def test_missing_interface_selection_is_inline(qapp, tmp_path, monkeypatch, tool):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    if tool == "subnet":
        tab.use_selected_subnet_calc_interface()
        label = tab.subnet_calc_status_label
    else:
        tab.use_selected_arp_subnet()
        label = tab.arp_input_error
    warning.assert_not_called()
    assert "목록" in label.text()
    assert "선택" in label.text()


@pytest.mark.parametrize("raw", ["", "   \n ", "!!!"])
def test_oui_missing_or_invalid_mac_is_inline(qapp, tmp_path, monkeypatch, raw):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.select_tool("oui")
    tab.show()
    qapp.processEvents()
    tab.oui_mac_edit.setPlainText(raw)
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    tab.lookup_oui_vendor()
    warning.assert_not_called()
    assert tab.oui_input_error.isVisibleTo(tab)
    assert "00:11:22:33:44:55" in tab.oui_input_error.text()
    assert qapp.focusWidget() is tab.oui_mac_edit


def test_arp_valid_input_keeps_execution_confirmation(qapp, tmp_path, monkeypatch):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    tab.arp_subnet_edit.setText("192.0.2.0/24")
    confirm = Mock(return_value=False)
    worker = Mock()
    monkeypatch.setattr("app.ui.tabs.diagnostics.tools.confirm_risky_action", confirm)
    monkeypatch.setattr(tab, "_start_worker", worker)
    tab.start_arp_scan()
    confirm.assert_called_once()
    worker.assert_not_called()


def test_each_tool_explains_its_purpose_in_plain_language(qapp, tmp_path):
    tab = DiagnosticsTab(build_fake_state(tmp_path))
    try:
        seen = set()
        for index in range(tab.diagnostic_tool_combo.count()):
            tab.diagnostic_tool_combo.setCurrentIndex(index)
            qapp.processEvents()
            hint = tab.diagnostic_tool_hint.text()
            assert hint, tab.diagnostic_tool_combo.itemText(index)
            seen.add(hint)
        assert len(seen) == tab.diagnostic_tool_combo.count()
        tab.select_tool("tcp")
        assert "포트" in tab.diagnostic_tool_hint.text()
    finally:
        tab.close()
