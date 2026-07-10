import logging
from threading import Event

import pytest

from app.models.network_models import NearbyAccessPoint
from app.services.wireless_service import WirelessService


class FakeWirelessService(WirelessService):
    def __init__(self, snapshots, on_scan=None):
        super().__init__(powershell=None, logger=logging.getLogger(__name__))
        self.snapshots = list(snapshots)
        self.on_scan = on_scan
        self.calls = 0
        self.include_oui_values = []

    def scan_nearby_access_points(self, include_oui=True, cancel_event=None):
        self.include_oui_values.append(include_oui)
        self.calls += 1
        snapshot = self.snapshots.pop(0)
        if self.on_scan is not None:
            self.on_scan()
        if isinstance(snapshot, Exception):
            raise snapshot
        return snapshot


class FakeClock:
    def __init__(self, start=100.0):
        self.now = float(start)
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


def ap(
    bssid,
    ssid,
    signal,
    channel,
    band="2.4 GHz",
    utilization=None,
    vendor="",
):
    return NearbyAccessPoint(
        interface_name="Wi-Fi",
        ssid=ssid,
        bssid=bssid,
        vendor=vendor,
        channel=channel,
        band=band,
        signal_percent=signal,
        channel_utilization_percent=utilization,
    )


def by_bssid(report, bssid):
    return next(access_point for access_point in report.observed_access_points if access_point.bssid == bssid)


def by_channel(report, channel):
    return next(summary for summary in report.channel_summaries if summary.channel == channel)


def test_scan_window_aggregates_access_point_signal_stats_and_channels(monkeypatch):
    monkeypatch.setattr("app.services.wireless_service.time.sleep", lambda _seconds: None)
    service = FakeWirelessService(
        [
            [
                ap("aa:aa:aa:aa:aa:aa", "Alpha", 40, "6", utilization=20, vendor="Acme"),
                ap("bb:bb:bb:bb:bb:bb", "Bravo", 80, "1"),
            ],
            [
                ap("aa:aa:aa:aa:aa:aa", "Alpha", 50, "6", utilization=40, vendor="Acme"),
                ap("bb:bb:bb:bb:bb:bb", "Bravo", 20, "1"),
                ap("cc:cc:cc:cc:cc:cc", "Charlie", 70, "11"),
            ],
            [
                ap("aa:aa:aa:aa:aa:aa", "Alpha", 60, "6", vendor="Acme"),
                ap("bb:bb:bb:bb:bb:bb", "Bravo", 75, "6"),
            ],
        ]
    )

    report = service.scan_nearby_access_points_window(duration_seconds=5, interval_seconds=2)

    assert report.duration_seconds == 5
    assert report.interval_seconds == 2
    assert report.sample_count == 3
    assert service.calls == 3
    assert all(sample.ok for sample in report.samples)

    alpha = by_bssid(report, "aa:aa:aa:aa:aa:aa")
    assert alpha.vendor == "Acme"
    assert alpha.sample_count == 3
    assert alpha.seen_ratio == 1.0
    assert alpha.average_signal_percent == pytest.approx(50.0)
    assert alpha.min_signal_percent == 40
    assert alpha.max_signal_percent == 60
    assert alpha.signal_range_percent == 20

    bravo = by_bssid(report, "bb:bb:bb:bb:bb:bb")
    assert bravo.channels == ["1", "6"]
    assert bravo.average_signal_percent == pytest.approx(58.333, abs=0.001)
    assert bravo.min_signal_percent == 20
    assert bravo.max_signal_percent == 80

    channel_6 = by_channel(report, "6")
    assert channel_6.access_point_count == 2
    assert channel_6.observation_count == 4
    assert channel_6.average_signal_percent == pytest.approx(56.25)
    assert channel_6.min_signal_percent == 40
    assert channel_6.max_signal_percent == 75
    assert channel_6.average_channel_utilization_percent == pytest.approx(30.0)

    assert [summary.channel for summary in report.channel_summaries] == ["1", "6", "11"]


def test_scan_window_uses_monotonic_deadline_without_scan_runtime_drift(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("app.services.wireless_service.time.monotonic", clock.monotonic)
    monkeypatch.setattr("app.services.wireless_service.time.sleep", clock.sleep)
    service = FakeWirelessService([[], [], []], on_scan=lambda: clock.advance(0.75))

    report = service.scan_nearby_access_points_window(duration_seconds=5, interval_seconds=2)

    assert [sample.elapsed_seconds for sample in report.samples] == pytest.approx([0.0, 2.0, 4.0])
    assert clock.sleeps == pytest.approx([1.25, 1.25, 0.25])
    assert report.actual_duration_seconds == pytest.approx(5.0)
    assert report.duration_seconds == 5


def test_scan_window_marks_unstable_access_points(monkeypatch):
    monkeypatch.setattr("app.services.wireless_service.time.sleep", lambda _seconds: None)
    service = FakeWirelessService(
        [
            [
                ap("aa:aa:aa:aa:aa:aa", "Stable", 51, "6"),
                ap("bb:bb:bb:bb:bb:bb", "Missing", 80, "1"),
                ap("cc:cc:cc:cc:cc:cc", "Moving", 45, "11"),
            ],
            [
                ap("aa:aa:aa:aa:aa:aa", "Stable", 55, "6"),
                ap("cc:cc:cc:cc:cc:cc", "Moving", 47, "36", band="5 GHz"),
            ],
            [
                ap("aa:aa:aa:aa:aa:aa", "Stable", 58, "6"),
                ap("bb:bb:bb:bb:bb:bb", "Missing", 20, "1"),
                ap("cc:cc:cc:cc:cc:cc", "Moving", 48, "36", band="5 GHz"),
            ],
        ]
    )

    report = service.scan_nearby_access_points_window(duration_seconds=5, interval_seconds=2)

    stable = by_bssid(report, "aa:aa:aa:aa:aa:aa")
    missing = by_bssid(report, "bb:bb:bb:bb:bb:bb")
    moving = by_bssid(report, "cc:cc:cc:cc:cc:cc")
    assert stable.unstable is False
    assert missing.unstable is True
    assert moving.unstable is True
    assert {access_point.bssid for access_point in report.unstable_access_points} == {
        "bb:bb:bb:bb:bb:bb",
        "cc:cc:cc:cc:cc:cc",
    }


def test_scan_window_returns_empty_report_for_empty_snapshots(monkeypatch):
    monkeypatch.setattr("app.services.wireless_service.time.sleep", lambda _seconds: None)
    service = FakeWirelessService([[], [], []])

    report = service.scan_nearby_access_points_window(duration_seconds=5, interval_seconds=2, include_oui=False)

    assert report.ok is True
    assert report.samples[0].access_point_count == 0
    assert report.observed_access_points == []
    assert report.channel_summaries == []
    assert service.include_oui_values == [False, False, False]


def test_scan_window_captures_scan_failures_in_report(monkeypatch):
    monkeypatch.setattr("app.services.wireless_service.time.sleep", lambda _seconds: None)
    service = FakeWirelessService([RuntimeError("radio unavailable"), RuntimeError("radio unavailable")])

    report = service.scan_nearby_access_points_window(duration_seconds=5, interval_seconds=3)

    assert report.ok is False
    assert report.sample_count == 2
    assert report.errors == ["radio unavailable", "radio unavailable"]
    assert [sample.error_message for sample in report.samples] == ["radio unavailable", "radio unavailable"]
    assert report.observed_access_points == []
    assert report.channel_summaries == []


def test_scan_window_clamps_bounds_and_sample_cap(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("app.services.wireless_service.time.monotonic", clock.monotonic)
    monkeypatch.setattr("app.services.wireless_service.time.sleep", clock.sleep)
    service = FakeWirelessService([[] for _ in range(25)])

    report = service.scan_nearby_access_points_window(duration_seconds=999, interval_seconds=1)

    assert report.duration_seconds == 120
    assert report.interval_seconds == 2
    assert report.sample_count == 25
    assert report.actual_duration_seconds == pytest.approx(48.0)
    assert report.sample_limit_reached is True
    assert service.calls == 25


def test_scan_window_stops_cooperatively_when_cancelled(monkeypatch):
    monkeypatch.setattr("app.services.wireless_service.time.sleep", lambda _seconds: None)
    cancel_event = Event()
    service = FakeWirelessService([[], [], []], on_scan=cancel_event.set)

    report = service.scan_nearby_access_points_window(
        duration_seconds=20,
        interval_seconds=5,
        cancel_event=cancel_event,
    )

    assert service.calls == 1
    assert report.sample_count == 1
    assert report.cancelled is True
    assert report.ok is False
    assert report.sample_limit_reached is False


def test_scan_window_honors_pre_cancel_without_scanning():
    cancel_event = Event()
    cancel_event.set()
    service = FakeWirelessService([])

    report = service.scan_nearby_access_points_window(
        duration_seconds=20,
        interval_seconds=5,
        cancel_event=cancel_event,
    )

    assert service.calls == 0
    assert report.sample_count == 0
    assert report.cancelled is True
