from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QThreadPool, Qt

from app.models.network_models import NearbyAccessPoint
from app.ui.tabs.wireless_tab import WirelessTab


class _FakeOuiService:
    def cache_summary(self) -> str:
        return "OUI cache"


def _build_wireless_tab(qapp) -> WirelessTab:
    state = SimpleNamespace(
        app_config={},
        oui_service=_FakeOuiService(),
        thread_pool=QThreadPool.globalInstance(),
    )
    return WirelessTab(state)


def test_nearby_ap_table_sorts_signal_as_number(qapp):
    tab = _build_wireless_tab(qapp)
    tab.nearby_access_points = [
        NearbyAccessPoint(ssid="LOW", bssid="00:00:00:00:00:01", signal_percent=5, channel="11"),
        NearbyAccessPoint(ssid="HIGH", bssid="00:00:00:00:00:02", signal_percent=99, channel="36"),
        NearbyAccessPoint(ssid="MID", bssid="00:00:00:00:00:03", signal_percent=70, channel="6"),
    ]

    tab._apply_nearby_view()
    tab.nearby_table.sortItems(3, Qt.SortOrder.DescendingOrder)

    assert tab.nearby_table.item(0, 0).text() == "HIGH"
    assert tab.nearby_table.item(1, 0).text() == "MID"
    assert tab.nearby_table.item(2, 0).text() == "LOW"


def test_nearby_ap_table_sorts_channel_as_number(qapp):
    tab = _build_wireless_tab(qapp)
    tab.nearby_access_points = [
        NearbyAccessPoint(ssid="CH112", bssid="00:00:00:00:00:01", signal_percent=80, channel="112"),
        NearbyAccessPoint(ssid="CH5", bssid="00:00:00:00:00:02", signal_percent=80, channel="5"),
        NearbyAccessPoint(ssid="CH48", bssid="00:00:00:00:00:03", signal_percent=80, channel="48"),
    ]

    tab._apply_nearby_view()
    tab.nearby_table.sortItems(6, Qt.SortOrder.AscendingOrder)

    assert [tab.nearby_table.item(row, 0).text() for row in range(3)] == ["CH5", "CH48", "CH112"]


def test_nearby_ap_column_visibility_is_saved_and_restored(qapp):
    tab = _build_wireless_tab(qapp)
    try:
        tab._set_nearby_column_visible(2, False)
        state = tab.save_ui_state()

        assert state["nearby_hidden_columns"] == [2]

        restored = _build_wireless_tab(qapp)
        try:
            restored.restore_ui_state(state)
            assert restored.nearby_table.isColumnHidden(2)
            assert restored.nearby_column_actions[2].isChecked() is False
        finally:
            restored.close()
    finally:
        tab.close()


def test_nearby_ap_empty_state_is_visible_before_scan(qapp):
    tab = _build_wireless_tab(qapp)
    try:
        tab.nearby_access_points = []
        tab._apply_nearby_view()

        assert not tab.nearby_empty_label.isHidden()
        assert "주변 AP 스캔" in tab.nearby_empty_label.text()
    finally:
        tab.close()


def test_nearby_ap_table_keeps_readable_height_and_splitter(qapp):
    tab = _build_wireless_tab(qapp)
    try:
        tab.resize(1280, 720)
        tab.show()
        qapp.processEvents()

        tab.nearby_access_points = [
            NearbyAccessPoint(ssid=f"AP-{index}", bssid=f"00:00:00:00:00:{index:02x}", signal_percent=80 - index)
            for index in range(20)
        ]
        tab._apply_nearby_view()

        assert tab.nearby_table.minimumHeight() >= 240
        assert tab.nearby_table.rowCount() == 20
        sizes = tab.wireless_main_splitter.sizes()
        assert len(sizes) == 2
        assert sizes[1] > sizes[0]
    finally:
        tab.close()


def test_wireless_status_cards_stay_compact_when_window_is_narrow(qapp):
    tab = _build_wireless_tab(qapp)
    try:
        tab.resize(860, 720)
        tab.show()
        qapp.processEvents()
        tab._rebuild_status_grid()

        positions = []
        for index in range(tab.status_grid.count()):
            item = tab.status_grid.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget in tab.status_cards.values():
                row, column, _row_span, _column_span = tab.status_grid.getItemPosition(index)
                positions.append((row, column))

        assert positions
        assert len({column for _row, column in positions}) >= 2
        assert max(row for row, _column in positions) + 1 <= 5
        assert max(card.maximumHeight() for card in tab.status_cards.values()) <= 58
    finally:
        tab.close()
