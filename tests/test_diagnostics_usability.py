from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from app.models.network_models import NearbyAccessPoint
from qa.offscreen.harness import OffscreenQaHarness


@pytest.fixture
def usability_harness(qapp, tmp_path: Path):
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


def test_quick_command_latest_request_owns_status_and_output(
    usability_harness: OffscreenQaHarness,
):
    harness = usability_harness
    window = harness._require_window()
    harness._navigate_main(1)
    tab = window.diagnostics_tab
    tab.select_diagnostic_tab("commands")

    harness._click(tab.quick_ipconfig_button)
    harness._click(tab.quick_route_button)
    assert harness.pool is not None
    assert len(harness.pool.pending) == 2

    latest_worker = harness.pool.pending.pop(1)
    latest_worker.run()
    harness._flush()
    stale_worker = harness.pool.pending.pop(0)
    stale_worker.run()
    harness._flush()

    assert "route print" in tab.quick_status_label.text()
    assert "0.0.0.0" in tab.tools_output.toPlainText()
    assert "Ethernet QA" not in tab.tools_output.toPlainText()


def test_diagnostic_and_wireless_failures_leave_retryable_inline_state(
    usability_harness: OffscreenQaHarness,
):
    harness = usability_harness
    window = harness._require_window()

    def fail(*_args, **_kwargs):
        raise RuntimeError("QA simulated network failure")

    harness._navigate_main(1)
    tab = window.diagnostics_tab
    tab.select_diagnostic_tab("ping")
    harness.state.ping_service.run_multi_ping = fail
    harness._paste(tab.ping_targets_edit, "Broken,192.0.2.99")
    harness._click(tab.ping_start_button)
    harness._release_next()

    assert "실패" in tab.ping_status_label.text()
    assert "QA simulated network failure" in tab.ping_status_label.text()
    assert tab.ping_empty_label.isVisibleTo(tab)
    assert tab.ping_targets_edit.isEnabled()
    assert tab.ping_start_button.isEnabled()

    tab.select_diagnostic_tab("dns")
    harness.state.dns_service.lookup = fail
    harness._paste(tab.dns_query_edit, "unreachable.example")
    harness._click(tab.dns_run_button)
    harness._release_next()

    assert "실패" in tab.dns_status_label.text()
    assert "실행 중" not in tab.dns_status_label.text()
    assert tab.dns_empty_label.isVisibleTo(tab)
    assert tab.dns_query_edit.isEnabled()
    assert tab.dns_run_button.isEnabled()

    harness._navigate_main(2)
    wireless_tab = window.wireless_tab
    harness.state.wireless_service.get_wireless_info = fail
    harness._click(wireless_tab.refresh_button)
    harness._release_next()

    assert "실패" in wireless_tab.wireless_status_label.text()
    assert "다시 시도" in wireless_tab.wireless_status_label.text()
    assert wireless_tab.refresh_button.isEnabled()


def test_wireless_queue_start_failure_uses_inline_retryable_state(
    usability_harness: OffscreenQaHarness,
):
    harness = usability_harness
    window = harness._require_window()
    assert harness.state is not None

    class FailingThreadPool:
        def start(self, _worker) -> None:
            raise RuntimeError("QA simulated queue rejection")

        def waitForDone(self, _msecs: int = -1) -> bool:  # noqa: N802 - Qt API
            return True

    original_pool = harness.state.thread_pool
    initial_message_count = len(harness._message_log)
    try:
        harness.state.thread_pool = FailingThreadPool()
        harness._navigate_main(2)
        tab = window.wireless_tab
        harness._click(tab.refresh_button)
        harness._flush()

        assert "QA simulated queue rejection" in tab.wireless_status_label.text()
        assert tab.refresh_button.isEnabled()
        assert len(harness._message_log) == initial_message_count
    finally:
        harness.state.thread_pool = original_pool


def test_compact_diagnostics_keep_core_actions_reachable_and_lock_inputs(
    usability_harness: OffscreenQaHarness,
):
    harness = usability_harness
    window = harness._require_window()
    window.resize(1024, 680)
    harness._navigate_main(1)
    tab = window.diagnostics_tab

    assert tab.quick_transfer_button.text() == "파일전송"
    assert (
        tab.quick_transfer_button.fontMetrics().horizontalAdvance(
            tab.quick_transfer_button.text()
        )
        < tab.quick_transfer_button.contentsRect().width()
    )

    tab.select_diagnostic_tab("ping")
    assert tab.ping_targets_edit.tabChangesFocus()
    tab.ping_targets_edit.setFocus()
    harness._flush()
    QTest.keyClick(tab.ping_targets_edit, Qt.Key.Key_Tab)
    harness._flush()
    assert harness.app.focusWidget() is not tab.ping_targets_edit

    harness._paste(
        tab.ping_targets_edit,
        "Gateway,192.0.2.1\nDNS,198.51.100.53",
    )
    harness._click(tab.ping_start_button)
    assert not tab.ping_targets_edit.isEnabled()
    assert not tab.ping_timeout_edit.isEnabled()
    assert "결과 0/2" in tab.ping_status_label.text()
    harness._release_next()
    assert tab.ping_targets_edit.isEnabled()
    assert "결과 2/2" in tab.ping_status_label.text()

    tab.ping_scroll_area.ensureWidgetVisible(tab.ping_csv_button)
    harness._flush()
    button_position = tab.ping_csv_button.mapTo(
        tab.ping_scroll_area.viewport(), QPoint(0, 0)
    )
    button_rect = tab.ping_csv_button.rect().translated(button_position)
    assert tab.ping_scroll_area.viewport().rect().intersects(button_rect)

    tab.select_diagnostic_tab("tcp")
    assert tab.tcp_targets_edit.tabChangesFocus()
    harness._paste(
        tab.tcp_targets_edit,
        "Web-A,192.0.2.10\nWeb-B,192.0.2.11",
    )
    harness._paste(tab.tcp_ports_edit, "22,443")
    harness._click(tab.tcp_start_button)
    assert not tab.tcp_targets_edit.isEnabled()
    assert not tab.tcp_ports_edit.isEnabled()
    assert "결과 0/4" in tab.tcp_status_label.text()
    harness._release_next()
    assert tab.tcp_targets_edit.isEnabled()
    assert "결과 4/4" in tab.tcp_status_label.text()

    assert tab.oui_mac_edit.tabChangesFocus()
    assert tab.scp_client_remote_sources_edit.tabChangesFocus()
    assert tab.tools_output.tabChangesFocus()


def test_ping_and_tcp_final_result_reapply_does_not_duplicate_rows(
    usability_harness: OffscreenQaHarness,
):
    harness = usability_harness
    window = harness._require_window()
    harness._navigate_main(1)
    tab = window.diagnostics_tab

    tab.select_diagnostic_tab("ping")
    harness._paste(
        tab.ping_targets_edit,
        "Gateway,192.0.2.1\nDNS,198.51.100.53",
    )
    harness._click(tab.ping_start_button)
    harness._release_next()
    assert tab.ping_table.rowCount() == 2
    assert len(tab.ping_row_map) == 2

    tab._finish_ping(list(tab.ping_results))
    assert tab.ping_table.rowCount() == 2
    assert len(tab.ping_row_map) == 2

    tab.select_diagnostic_tab("tcp")
    harness._paste(
        tab.tcp_targets_edit,
        "Web-A,192.0.2.10\nWeb-B,192.0.2.11",
    )
    harness._paste(tab.tcp_ports_edit, "22,443")
    harness._click(tab.tcp_start_button)
    harness._release_next()
    assert tab.tcp_table.rowCount() == 4
    assert len(tab.tcp_row_map) == 4

    tab._finish_tcp(list(tab.tcp_results))
    assert tab.tcp_table.rowCount() == 4
    assert len(tab.tcp_row_map) == 4


def test_wireless_large_result_summary_stays_compact_and_cells_keep_full_text(
    usability_harness: OffscreenQaHarness,
):
    harness = usability_harness
    window = harness._require_window()
    window.resize(1024, 680)
    harness._navigate_main(2)
    tab = window.wireless_tab
    harness._click(tab.refresh_button)
    harness._release_next()

    tab.nearby_access_points = [
        NearbyAccessPoint(
            interface_name="Wi-Fi QA",
            ssid=f"CORPORATE-NETWORK-{index:02d}-WITH-A-VERY-LONG-NAME",
            bssid=f"02:11:22:33:44:{index:02X}",
            vendor="Very Long Wireless Manufacturer Incorporated",
            authentication="WPA3-Enterprise Suite-B",  # gitleaks:allow -- public Wi-Fi mode label
            encryption="GCMP-256",
            radio_standard="802.11be Multi-Link",
            band="6 GHz" if index % 3 == 0 else "5 GHz",
            channel=str(1 + index * 4),
            signal_percent=max(5, 95 - index * 2),
            connected_stations=index,
            channel_utilization_percent=(index * 7) % 100,
        )
        for index in range(1, 31)
    ]
    tab._apply_nearby_view()
    harness._flush()

    assert "표시 30 / 전체 30" in tab.nearby_summary_label.text()
    assert "주요 채널" in tab.nearby_summary_label.text()
    assert len(tab.nearby_summary_label.text()) < 240
    assert len(tab.nearby_summary_label.toolTip()) > len(
        tab._compact_channel_summary(tab.nearby_access_points)
    )
    assert tab.nearby_table.item(0, 0).toolTip() == tab.nearby_table.item(
        0, 0
    ).text()
