from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, QThreadPool, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QWidget

from app.models.network_models import NearbyAccessPoint, WirelessInfo
from app.ui.common.theme import APP_STYLE_SHEET
from app.ui.tabs.wireless_tab import WirelessTab


def _state():
    return SimpleNamespace(
        app_config={},
        thread_pool=QThreadPool.globalInstance(),
        oui_service=SimpleNamespace(cache_summary=lambda: "IEEE OUI SHA256=" + "a" * 64),
    )


def _settle(qapp):
    for _ in range(5):
        qapp.processEvents()


def _global_rect(widget):
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _assert_cards_readable(tab):
    for card in tab.status_cards.values():
        assert card.isVisibleTo(tab)
        title, value = card.findChildren(QLabel)
        assert not _global_rect(title).intersects(_global_rect(value))
        for label in (title, value):
            relative_rect = QRect(label.mapTo(card, QPoint(0, 0)), label.size())
            assert card.rect().contains(relative_rect)
            text_rect = label.fontMetrics().boundingRect(
                QRect(0, 0, label.contentsRect().width(), 10000),
                Qt.TextFlag.TextWordWrap,
                label.text(),
            )
            assert text_rect.height() <= label.contentsRect().height()


@pytest.mark.parametrize("width,height", [(1024, 680), (1280, 800), (1600, 900)])
def test_expanded_wireless_sections_remain_readable_and_scroll_to_end(qapp, width, height):
    window = QMainWindow()
    window.setStyleSheet(APP_STYLE_SHEET)
    central = QWidget()
    body = QHBoxLayout(central)
    body.setContentsMargins(0, 0, 0, 0)
    sidebar = QWidget()
    sidebar.setFixedWidth(220)
    body.addWidget(sidebar)
    tab = WirelessTab(_state())
    body.addWidget(tab, 1)
    window.setCentralWidget(central)
    window.resize(width, height)
    window.show()
    _settle(qapp)
    assert window.width() == width
    assert window.height() == height
    tab._update_wireless_view(WirelessInfo(
        interface_name="Corporate wireless network adapter with multiple radio interfaces",
        description="Wi-Fi 7 test adapter",
        state="연결됨",
        ssid="CORPORATE-NETWORK-WITH-A-LONG-SSID",
        bssid="02:11:22:33:44:55",
        signal_percent=85,
        channel="149",
        band="5 GHz",
        radio_type="802.11be Multi-Link",
        receive_rate_mbps="2401.0",
        transmit_rate_mbps="1200.0",
    ))
    tab.nearby_access_points = [
        NearbyAccessPoint(ssid=f"AP-{index:02}", bssid=f"02:11:22:33:44:{index:02X}",
                          signal_percent=95 - index, channel="149", band="5 GHz")
        for index in range(30)
    ]
    tab._apply_nearby_view()
    for index in range(20):
        tab._append_change_log(f"연결 변화 {index}: 이전 AP에서 현재 AP로 전환됨")

    sections = (tab.status_details_section, tab.change_log_section, tab.nearby_options_section)
    for _ in range(3):
        for section in sections:
            section.setExpanded(False)
        _settle(qapp)
        for section in sections:
            section.setExpanded(True)
        _settle(qapp)
        _assert_cards_readable(tab)
        assert tab.change_log.viewport().height() >= 120
        assert tab.nearby_table.viewport().height() >= 180
        assert _global_rect(tab.status_group).bottom() < _global_rect(tab.change_log_section).top()
        assert _global_rect(tab.change_log_section).bottom() < _global_rect(tab.nearby_group).top()
        assert _global_rect(tab.nearby_options_section).bottom() < _global_rect(tab.nearby_table).top()
        assert tab.wireless_scroll_area.horizontalScrollBar().maximum() == 0

        # Options can all be reached at their real coordinates, including the
        # rightmost control; parent visibility alone would miss clipped inputs.
        for widget in (tab.nearby_band_filter, tab.nearby_security_filter, tab.nearby_sort_combo,
                       tab.nearby_connected_only_check, tab.nearby_column_button,
                       tab.nearby_interval_spin, tab.nearby_refresh_oui_button):
            # Spin boxes expose their smaller line-edit as the focus rect;
            # leave room for the complete control and its buttons as well.
            tab.wireless_scroll_area.ensureWidgetVisible(widget, 0, widget.height())
            _settle(qapp)
            viewport = tab.wireless_scroll_area.viewport()
            rect = QRect(widget.mapTo(viewport, QPoint(0, 0)), widget.size())
            assert viewport.rect().contains(rect)

        outer_scroll = tab.wireless_scroll_area.verticalScrollBar()
        assert outer_scroll.maximum() > 0
        outer_scroll.setValue(outer_scroll.maximum())
        _settle(qapp)
        table_bottom = tab.nearby_table.mapTo(tab.wireless_scroll_area.viewport(), tab.nearby_table.rect().bottomLeft())
        assert table_bottom.y() <= tab.wireless_scroll_area.viewport().height()
        last_item = tab.nearby_table.item(tab.nearby_table.rowCount() - 1, 9)
        tab.nearby_table.scrollToItem(last_item)
        _settle(qapp)
        assert tab.nearby_table.viewport().rect().contains(tab.nearby_table.visualItemRect(last_item))
    assert "SHA256" not in tab.nearby_summary_label.text()
    assert "SHA256" in tab.nearby_summary_label.toolTip()
    window.close()


def test_wireless_selection_and_expanded_state_survive_refresh_and_restore(qapp):
    tab = WirelessTab(_state())
    tab.nearby_access_points = [
        NearbyAccessPoint(ssid="A", bssid="00:11:22:33:44:55", signal_percent=80),
        NearbyAccessPoint(ssid="B", bssid="00:11:22:33:44:66", signal_percent=70),
    ]
    tab._apply_nearby_view()
    tab.nearby_table.selectRow(1)
    selected_bssid = tab.nearby_table.item(tab.nearby_table.currentRow(), 1).text()
    tab.nearby_access_points.reverse()
    tab._apply_nearby_view()
    assert tab.nearby_table.item(tab.nearby_table.currentRow(), 1).text() == selected_bssid
    tab.status_details_section.setExpanded(True)
    tab.change_log_section.setExpanded(True)
    tab.nearby_options_section.setExpanded(True)
    restored = WirelessTab(_state())
    restored.restore_ui_state(tab.save_ui_state())
    assert restored.status_details_section.isExpanded()
    assert restored.change_log_section.isExpanded()
    assert restored.nearby_options_section.isExpanded()


def test_wireless_cards_reflow_between_four_and_two_columns(qapp):
    tab = WirelessTab(_state())
    tab.resize(1100, 800)
    tab.show()
    _settle(qapp)
    assert tab._status_grid_columns == 4
    tab.resize(760, 680)
    _settle(qapp)
    assert tab._status_grid_columns == 2
    assert tab.status_grid.count() == 4
    assert tab.status_detail_grid.count() == 6
    tab.resize(1100, 800)
    _settle(qapp)
    assert tab._status_grid_columns == 4


@pytest.mark.parametrize("percent,color,strength", [
    (0, "#b71c1c", "약함"),
    (39, "#b71c1c", "약함"),
    (40, "#ef6c00", "주의"),
    (69, "#ef6c00", "주의"),
    (70, "#1b5e20", "좋음"),
    (100, "#1b5e20", "좋음"),
])
def test_current_signal_matches_ap_thresholds_and_explains_color(qapp, percent, color, strength):
    tab = WirelessTab(_state())
    tab.setStyleSheet(APP_STYLE_SHEET)
    tab.show()
    tab._update_wireless_view(WirelessInfo(
        state="connected", ssid="Office", bssid="00:11:22:33:44:55",
        signal_percent=percent, channel="36", radio_type="802.11ax",
    ))
    tab._update_nearby_access_points([
        NearbyAccessPoint(ssid="Office", bssid="00:11:22:33:44:55", signal_percent=percent),
    ])
    _settle(qapp)
    signal = tab.info_labels["signal"]
    assert signal.text() == f"{percent}% · {strength}"
    assert signal.palette().color(QPalette.ColorRole.WindowText).name() == color
    assert strength in signal.accessibleDescription()
    assert "70%" in signal.toolTip()
    ap_signal = tab.nearby_table.item(0, 3)
    assert ap_signal.text() == f"{percent}%"
    assert ap_signal.foreground().color().name() == color
    assert strength in ap_signal.toolTip()
    assert strength in ap_signal.data(Qt.ItemDataRole.AccessibleTextRole)
    for key in ("channel", "ssid", "radio_type"):
        assert tab.info_labels[key].styleSheet() == ""
        assert strength not in tab.info_labels[key].text()
    tab.close()


@pytest.mark.parametrize("state,percent,rssi,expected", [
    ("disconnected", 80, "-55", "-"),
    ("연결 끊김", 80, "-55", "-"),
    ("미연결", None, "", "-"),
    ("", None, "", "-"),
    ("연결됨", None, "", "-"),
    ("connected", None, "-62", "-62 dBm"),
])
def test_current_signal_clears_stale_color_when_disconnected_or_percent_missing(qapp, state, percent, rssi, expected):
    tab = WirelessTab(_state())
    tab.setStyleSheet(APP_STYLE_SHEET)
    tab.show()
    _settle(qapp)
    signal = tab.info_labels["signal"]
    default_color = signal.palette().color(QPalette.ColorRole.WindowText)
    tab._update_wireless_view(WirelessInfo(state="연결됨", signal_percent=85))
    _settle(qapp)
    assert signal.palette().color(QPalette.ColorRole.WindowText).name() == "#1b5e20"
    tab._update_wireless_view(WirelessInfo(
        state=state, bssid="00:11:22:33:44:55", signal_percent=percent, rssi=rssi,
    ))
    _settle(qapp)
    assert signal.text() == expected
    assert signal.styleSheet() == ""
    assert signal.palette().color(QPalette.ColorRole.WindowText) == default_color
    assert "정보 없음" in signal.accessibleDescription()
    assert "좋음" not in signal.toolTip()
    tab.close()
