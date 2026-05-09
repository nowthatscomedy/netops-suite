from __future__ import annotations

import ctypes
import logging
import subprocess
import time
from ctypes import wintypes

from app.models.network_models import NearbyAccessPoint, WirelessInfo
from app.services.oui_service import OuiService
from app.services.powershell_service import PowerShellService
from app.utils.parser import parse_netsh_wlan_networks_output, parse_netsh_wlan_output
from app.utils.process_utils import decode_windows_command_output, no_window_creationflags


class WirelessService:
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
            creationflags=no_window_creationflags(),
        )
        raw_output = decode_windows_command_output(completed.stdout or completed.stderr)
        info = parse_netsh_wlan_output(raw_output)
        if not info.state:
            info.state = "사용 불가" if completed.returncode != 0 else "연결 안 됨"
        if not info.interface_name and info.description:
            info.interface_name = info.description
        return info

    def scan_nearby_access_points(self) -> list[NearbyAccessPoint]:
        self._request_native_wifi_scan()
        completed = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True,
            text=False,
            creationflags=no_window_creationflags(),
        )
        raw_output = decode_windows_command_output(completed.stdout or completed.stderr)
        access_points = parse_netsh_wlan_networks_output(raw_output)
        if self.oui_service is not None:
            for access_point in access_points:
                access_point.vendor = self.oui_service.lookup_vendor(access_point.bssid)
        return access_points

    def _request_native_wifi_scan(self) -> None:
        """Ask Windows to refresh Wi-Fi scan results before reading the netsh cache."""
        try:
            scan_count = self._wlan_scan_all_interfaces()
        except Exception as exc:
            self.logger.debug("Native Wi-Fi scan request failed: %s", exc)
            return
        if scan_count:
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
