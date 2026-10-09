"""Build sample inventory rows that match the selected device and task."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

SAMPLE_DEVICE_IPS = ("192.0.2.10", "192.0.2.11")
_DEFAULT_PORTS = {"ssh": 22, "telnet": 23}
SAMPLE_BASE_COLUMNS = (
    "ip",
    "vendor",
    "os",
    "model",
    "connection_type",
    "port",
    "username",
    "password",
    "enable_password",
)
# Vendors whose handlers never enter a privileged mode.
_NO_ENABLE_VENDORS = frozenset({"alcatel-lucent", "axgate"})
_INTERFACE_EXAMPLES = {
    "cisco": ("GigabitEthernet1/0/1", "GigabitEthernet1/0/2"),
    "juniper": ("ge-0/0/1", "ge-0/0/2"),
    "aruba": ("1/1/1", "1/1/2"),
    "alcatel-lucent": ("1/1", "1/2"),
    "ruckus": ("ethernet 1/1/1", "ethernet 1/1/2"),
}


def sample_connection_type(connection_types: Iterable[str]) -> str:
    """Prefer SSH unless the device only has a Telnet handler."""
    available = {str(value).strip().lower() for value in connection_types if value}
    if available and "ssh" not in available and "telnet" in available:
        return "telnet"
    return "ssh"


def build_sample_inventory_rows(
    *,
    vendor: str,
    os_name: str,
    model: str = "",
    mode: str = "inspection",
    connection_type: str = "ssh",
    variable_names: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Return two example rows with exactly the columns the task needs.

    Custom-command mode adds one column per ``{{ variable }}`` used in the
    commands, filled with an example value that suits the vendor.
    """
    vendor_key = str(vendor or "").strip().lower() or "cisco"
    os_key = str(os_name or "").strip().lower() or "ios"
    connection = connection_type if connection_type in _DEFAULT_PORTS else "ssh"
    extra_columns: list[str] = []
    if mode == "custom_commands":
        for name in variable_names:
            column = str(name).strip()
            if column and column not in SAMPLE_BASE_COLUMNS and column not in extra_columns:
                extra_columns.append(column)

    rows: list[dict[str, Any]] = []
    for index, ip in enumerate(SAMPLE_DEVICE_IPS):
        row: dict[str, Any] = {
            "ip": ip,
            "vendor": vendor_key,
            "os": os_key,
            "model": str(model or "").strip(),
            "connection_type": connection,
            "port": _DEFAULT_PORTS[connection],
            "username": "admin",
            "password": "CHANGE_ME_PASSWORD",
            "enable_password": (
                "" if vendor_key in _NO_ENABLE_VENDORS else "CHANGE_ME_ENABLE_PASSWORD"
            ),
        }
        for column in extra_columns:
            row[column] = example_value_for_variable(column, vendor_key, index)
        rows.append(row)
    return rows


def example_value_for_variable(name: str, vendor: str = "", index: int = 0) -> Any:
    """Guess a realistic example value from a command variable name."""
    key = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    if "interface" in key or key in {"port_name", "intf", "ifname", "if_name"}:
        examples = _INTERFACE_EXAMPLES.get(vendor, ("1/1", "1/2"))
        return examples[index % len(examples)]
    if "vlan" in key:
        return 100 + index * 10
    if "gateway" in key or key in {"gw", "default_gateway"}:
        return "192.0.2.1"
    if "mac" in key:
        return f"00:11:22:33:44:{0x55 + index:02x}"
    if "mask" in key:
        return "255.255.255.0"
    if key.endswith("ip") or "ip_address" in key or key.startswith("ip_") or "address" in key:
        return f"198.51.100.{10 + index}"
    if "host" in key or key.endswith("name"):
        return f"SW-{index + 1:02d}"
    if "desc" in key:
        return f"sample-{index + 1}"
    return f"{key or 'value'}-{index + 1}"
