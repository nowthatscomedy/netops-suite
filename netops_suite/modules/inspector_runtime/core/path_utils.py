"""Path helpers for the migrated Inspector runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_dir() -> Path:
    """Return the writable Inspector app data directory.

    The unified GUI sets NETOPS_SUITE_INSPECTOR_DATA_DIR so settings,
    custom_rules, and user parser files live under the NetOps Suite data root.
    """
    configured = os.environ.get("NETOPS_SUITE_INSPECTOR_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
