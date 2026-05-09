"""Compatibility wrapper for legacy synchronous Telnet handlers."""

from __future__ import annotations

import importlib


def _load_telnet_class():
    try:
        telnetlib = importlib.import_module("telnetlib3.telnetlib")
    except ImportError as exc:
        raise RuntimeError(
            "telnetlib3 is required for Telnet support. "
            "Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc
    return telnetlib.Telnet


def Telnet(*args, **kwargs):
    return _load_telnet_class()(*args, **kwargs)

__all__ = ["Telnet"]
