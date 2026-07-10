from __future__ import annotations

from collections import Counter, defaultdict
import ctypes
import logging
import math
import subprocess
import threading
import time
from ctypes import wintypes

from app.models.network_models import (
    NearbyAccessPoint,
    WirelessChannelSummary,
    WirelessInfo,
    WirelessObservedAccessPoint,
    WirelessScanReport,
    WirelessScanSample,
)
from app.services.oui_service import OuiService
from app.services.powershell_service import PowerShellService
from app.utils.parser import parse_netsh_wlan_networks_output, parse_netsh_wlan_output
from app.utils.process_utils import decode_windows_command_output, no_window_creationflags


class WirelessService:
    COMMAND_TIMEOUT_SEC = 15

    def __init__(
        self,
        powershell: PowerShellService,
        logger: logging.Logger,
        oui_service: OuiService | None = None,
    ) -> None:
        self.powershell = powershell
        self.logger = logger
        self.oui_service = oui_service

    def get_wireless_info(self) -> WirelessInfo:
        completed = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=False,
            timeout=self.COMMAND_TIMEOUT_SEC,
            creationflags=no_window_creationflags(),
        )
        raw_output = decode_windows_command_output(completed.stdout or completed.stderr)
        info = parse_netsh_wlan_output(raw_output)
        if not info.state:
            info.state = "사용 불가" if completed.returncode != 0 else "연결 안 됨"
        if not info.interface_name and info.description:
            info.interface_name = info.description
        return info

    def scan_nearby_access_points(
        self,
        include_oui: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> list[NearbyAccessPoint]:
        self._request_native_wifi_scan(cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return []
        completed = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True,
            text=False,
            timeout=self.COMMAND_TIMEOUT_SEC,
            creationflags=no_window_creationflags(),
        )
        raw_output = decode_windows_command_output(completed.stdout or completed.stderr)
        access_points = parse_netsh_wlan_networks_output(raw_output)
        if include_oui and self.oui_service is not None:
            for access_point in access_points:
                access_point.vendor = self.oui_service.lookup_vendor(access_point.bssid)
        return access_points

    def scan_nearby_access_points_window(
        self,
        duration_seconds: int,
        interval_seconds: int,
        include_oui: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> WirelessScanReport:
        duration = min(120, max(5, int(duration_seconds)))
        interval = min(30, max(2, int(interval_seconds)))
        requested_sample_count = max(1, math.ceil(duration / interval))
        planned_sample_count = min(25, requested_sample_count)

        samples: list[WirelessScanSample] = []
        errors: list[str] = []
        started_at = time.monotonic()
        deadline = started_at + duration
        cancelled = False
        for sample_index in range(planned_sample_count):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break

            scheduled_at = started_at + (sample_index * interval)
            now = time.monotonic()
            if sample_index > 0 and now >= deadline:
                break
            if self._wait_for_scan_slot(scheduled_at - now, cancel_event):
                cancelled = True
                break

            now = time.monotonic()
            if sample_index > 0 and now >= deadline:
                break
            elapsed_seconds = now - started_at
            try:
                access_points = self.scan_nearby_access_points(
                    include_oui=include_oui,
                    cancel_event=cancel_event,
                )
                samples.append(
                    WirelessScanSample(
                        sample_index=sample_index,
                        elapsed_seconds=elapsed_seconds,
                        access_points=access_points,
                    )
                )
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                errors.append(message)
                self.logger.debug("Wireless scan sample %s failed: %s", sample_index, message)
                samples.append(
                    WirelessScanSample(
                        sample_index=sample_index,
                        elapsed_seconds=elapsed_seconds,
                        error_message=message,
                    )
                )

            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break

        sample_limit_reached = (
            not cancelled
            and requested_sample_count > planned_sample_count
            and len(samples) == planned_sample_count
        )
        # A non-capped scan represents the full requested observation window,
        # even when its final sampling slot occurs shortly before the deadline.
        if not cancelled and not sample_limit_reached:
            cancelled = self._wait_for_scan_slot(deadline - time.monotonic(), cancel_event)

        observed_access_points = self._aggregate_observed_access_points(samples)
        unstable_access_points = [access_point for access_point in observed_access_points if access_point.unstable]
        channel_summaries = self._summarize_channels(samples)
        return WirelessScanReport(
            duration_seconds=duration,
            interval_seconds=interval,
            sample_count=len(samples),
            actual_duration_seconds=time.monotonic() - started_at,
            cancelled=cancelled,
            sample_limit_reached=sample_limit_reached,
            samples=samples,
            observed_access_points=observed_access_points,
            channel_summaries=channel_summaries,
            unstable_access_points=unstable_access_points,
            errors=errors,
        )

    @staticmethod
    def _wait_for_scan_slot(
        seconds: float,
        cancel_event: threading.Event | None,
    ) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return True
        if seconds <= 0:
            return False
        if cancel_event is not None:
            return cancel_event.wait(seconds)
        time.sleep(seconds)
        return False

    def _aggregate_observed_access_points(
        self,
        samples: list[WirelessScanSample],
    ) -> list[WirelessObservedAccessPoint]:
        grouped: dict[str, list[tuple[WirelessScanSample, NearbyAccessPoint]]] = defaultdict(list)
        for sample in samples:
            for access_point in sample.access_points:
                grouped[self._access_point_key(access_point)].append((sample, access_point))

        successful_sample_indexes = {sample.sample_index for sample in samples if sample.ok}
        total_samples = len(successful_sample_indexes)
        observed_access_points: list[WirelessObservedAccessPoint] = []
        for observations in grouped.values():
            observations.sort(key=lambda item: item[0].sample_index)
            first_sample, first_access_point = observations[0]
            last_sample, last_access_point = observations[-1]
            signal_values = [
                int(access_point.signal_percent)
                for _, access_point in observations
                if access_point.signal_percent is not None
            ]
            min_signal = min(signal_values) if signal_values else None
            max_signal = max(signal_values) if signal_values else None
            signal_range = max_signal - min_signal if min_signal is not None and max_signal is not None else None
            channels = self._ordered_values(access_point.channel for _, access_point in observations)
            observed_channel = self._most_common_value(access_point.channel for _, access_point in observations)
            sample_indexes = {sample.sample_index for sample, _ in observations}
            seen_sample_count = len(sample_indexes)
            unstable = (
                (total_samples > 0 and seen_sample_count < total_samples)
                or len(channels) > 1
                or (signal_range is not None and signal_range >= 20)
            )
            observed_access_points.append(
                WirelessObservedAccessPoint(
                    bssid=last_access_point.bssid or first_access_point.bssid,
                    ssid=last_access_point.ssid or first_access_point.ssid,
                    vendor=last_access_point.vendor or first_access_point.vendor,
                    interface_name=last_access_point.interface_name or first_access_point.interface_name,
                    authentication=last_access_point.authentication or first_access_point.authentication,
                    encryption=last_access_point.encryption or first_access_point.encryption,
                    radio_standard=last_access_point.radio_standard or first_access_point.radio_standard,
                    band=self._most_common_value(access_point.band for _, access_point in observations),
                    channel=observed_channel,
                    channels=channels,
                    sample_count=seen_sample_count,
                    seen_ratio=seen_sample_count / total_samples if total_samples else 0.0,
                    first_seen_seconds=first_sample.elapsed_seconds,
                    last_seen_seconds=last_sample.elapsed_seconds,
                    average_signal_percent=(sum(signal_values) / len(signal_values)) if signal_values else None,
                    min_signal_percent=min_signal,
                    max_signal_percent=max_signal,
                    signal_range_percent=signal_range,
                    unstable=unstable,
                )
            )

        return sorted(
            observed_access_points,
            key=lambda access_point: (
                -(access_point.average_signal_percent if access_point.average_signal_percent is not None else -1),
                access_point.ssid.lower(),
                access_point.bssid.lower(),
            ),
        )

    def _summarize_channels(self, samples: list[WirelessScanSample]) -> list[WirelessChannelSummary]:
        grouped: dict[str, list[NearbyAccessPoint]] = defaultdict(list)
        for sample in samples:
            for access_point in sample.access_points:
                grouped[access_point.channel or "-"].append(access_point)

        summaries: list[WirelessChannelSummary] = []
        for channel, access_points in grouped.items():
            signal_values = [
                int(access_point.signal_percent)
                for access_point in access_points
                if access_point.signal_percent is not None
            ]
            utilization_values = [
                int(access_point.channel_utilization_percent)
                for access_point in access_points
                if access_point.channel_utilization_percent is not None
            ]
            unique_access_points = {self._access_point_key(access_point) for access_point in access_points}
            summaries.append(
                WirelessChannelSummary(
                    channel=channel,
                    band=self._most_common_value(access_point.band for access_point in access_points),
                    access_point_count=len(unique_access_points),
                    observation_count=len(access_points),
                    average_signal_percent=(sum(signal_values) / len(signal_values)) if signal_values else None,
                    min_signal_percent=min(signal_values) if signal_values else None,
                    max_signal_percent=max(signal_values) if signal_values else None,
                    average_channel_utilization_percent=(
                        sum(utilization_values) / len(utilization_values) if utilization_values else None
                    ),
                )
            )
        return sorted(summaries, key=lambda summary: self._channel_sort_key(summary.channel))

    @staticmethod
    def _access_point_key(access_point: NearbyAccessPoint) -> str:
        if access_point.bssid:
            return access_point.bssid.strip().lower()
        return "|".join(
            [
                access_point.interface_name.strip().lower(),
                access_point.ssid.strip().lower(),
                access_point.channel.strip().lower(),
            ]
        )

    @staticmethod
    def _ordered_values(values) -> list[str]:
        ordered: list[str] = []
        for value in values:
            if value and value not in ordered:
                ordered.append(value)
        return ordered

    @staticmethod
    def _most_common_value(values) -> str:
        cleaned_values = [value for value in values if value]
        if not cleaned_values:
            return ""
        return Counter(cleaned_values).most_common(1)[0][0]

    @staticmethod
    def _channel_sort_key(channel: str) -> tuple[int, int | str]:
        try:
            return (0, int(channel))
        except ValueError:
            return (1, channel)

    def _request_native_wifi_scan(self, cancel_event: threading.Event | None = None) -> None:
        """Ask Windows to refresh Wi-Fi scan results before reading the netsh cache."""
        try:
            scan_count = self._wlan_scan_all_interfaces()
        except Exception as exc:
            self.logger.debug("Native Wi-Fi scan request failed: %s", exc)
            return
        if scan_count:
            if cancel_event is not None:
                cancel_event.wait(2.0)
            else:
                time.sleep(2.0)

    def _wlan_scan_all_interfaces(self) -> int:
        wlanapi = ctypes.WinDLL("wlanapi.dll")

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8),
            ]

        class WLAN_INTERFACE_INFO(ctypes.Structure):
            _fields_ = [
                ("InterfaceGuid", GUID),
                ("strInterfaceDescription", wintypes.WCHAR * 256),
                ("isState", wintypes.DWORD),
            ]

        class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
            _fields_ = [
                ("dwNumberOfItems", wintypes.DWORD),
                ("dwIndex", wintypes.DWORD),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
            ]

        WlanOpenHandle = wlanapi.WlanOpenHandle
        WlanOpenHandle.argtypes = [
            wintypes.DWORD,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.HANDLE),
        ]
        WlanOpenHandle.restype = wintypes.DWORD

        WlanEnumInterfaces = wlanapi.WlanEnumInterfaces
        WlanEnumInterfaces.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)),
        ]
        WlanEnumInterfaces.restype = wintypes.DWORD

        WlanScan = wlanapi.WlanScan
        WlanScan.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(GUID),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        WlanScan.restype = wintypes.DWORD

        WlanFreeMemory = wlanapi.WlanFreeMemory
        WlanFreeMemory.argtypes = [wintypes.LPVOID]
        WlanFreeMemory.restype = None

        WlanCloseHandle = wlanapi.WlanCloseHandle
        WlanCloseHandle.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        WlanCloseHandle.restype = wintypes.DWORD

        negotiated_version = wintypes.DWORD()
        client_handle = wintypes.HANDLE()
        result = WlanOpenHandle(2, None, ctypes.byref(negotiated_version), ctypes.byref(client_handle))
        if result != 0:
            self.logger.debug("WlanOpenHandle failed: %s", result)
            return 0

        interface_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        scan_count = 0
        try:
            result = WlanEnumInterfaces(client_handle, None, ctypes.byref(interface_list))
            if result != 0 or not interface_list:
                self.logger.debug("WlanEnumInterfaces failed: %s", result)
                return 0

            count = int(interface_list.contents.dwNumberOfItems)
            if count <= 0:
                return 0
            info_array_type = WLAN_INTERFACE_INFO * count
            interfaces = ctypes.cast(
                ctypes.addressof(interface_list.contents.InterfaceInfo),
                ctypes.POINTER(info_array_type),
            ).contents

            for interface in interfaces:
                result = WlanScan(client_handle, ctypes.byref(interface.InterfaceGuid), None, None, None)
                if result == 0:
                    scan_count += 1
                else:
                    self.logger.debug("WlanScan failed for %s: %s", interface.strInterfaceDescription, result)
            return scan_count
        finally:
            if interface_list:
                WlanFreeMemory(interface_list)
            WlanCloseHandle(client_handle, None)

    def list_wireless_adapters(self) -> list[str]:
        script = """
Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -match 'Wi-?Fi|Wireless|WLAN|802\\.11' -or
    $_.InterfaceDescription -match 'Wi-?Fi|Wireless|WLAN|802\\.11'
  } |
  Select-Object -ExpandProperty Name |
  ConvertTo-Json -Compress
"""
        data = self.powershell.run_json(script, timeout=15)
        if not data:
            return []
        if isinstance(data, str):
            return [data]
        return [str(item) for item in data]
