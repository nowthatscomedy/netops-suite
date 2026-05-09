from __future__ import annotations

import ctypes
import sys
from pathlib import Path


def is_running_as_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    script_path = Path(sys.argv[0]).resolve()
    args = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    executable = sys.executable

    if getattr(sys, "frozen", False):
        parameters = args
    else:
        parameters = f'"{script_path}" {args}'.strip()

    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, parameters, None, 1)
    return int(result) > 32
