"""Compatibility wrapper for legacy synchronous Telnet handlers."""

from __future__ import annotations

try:
    from telnetlib3 import telnetlib as _telnetlib
except Exception:  # pragma: no cover - only used when telnetlib3 is unavailable.
    import telnetlib as _telnetlib  # type: ignore[no-redef]


Telnet = _telnetlib.Telnet
