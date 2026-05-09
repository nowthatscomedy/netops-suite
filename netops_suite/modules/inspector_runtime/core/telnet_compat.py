"""Compatibility wrapper for legacy synchronous Telnet handlers."""

from __future__ import annotations

try:
    from telnetlib3 import telnetlib as _telnetlib
except ImportError as exc:  # pragma: no cover - dependency guard.
    raise ImportError(
        "telnetlib3 is required for Telnet support. "
        "Install dependencies with `python -m pip install -r requirements.txt`."
    ) from exc


Telnet = _telnetlib.Telnet

__all__ = ["Telnet"]
