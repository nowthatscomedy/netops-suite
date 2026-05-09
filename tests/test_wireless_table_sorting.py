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
