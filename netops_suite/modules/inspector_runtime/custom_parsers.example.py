"""Reference examples for user-defined Inspector parsing functions.

Copy the function you need into:
%LOCALAPPDATA%\\NetOps Suite\\inspector\\custom_parsers\\<function_name>.py

Function names must start with ``parsing_``. Each function receives the full
command output as a string and returns either one Excel cell value or a dict of
``{"column name": "value"}``.
"""

from __future__ import annotations


def parsing_reference_environment_summary(output: str) -> str:
    """Return a compact status summary from environment-like command output."""
    keywords = ("ok", "normal", "good")
    warnings = ("fail", "fault", "alarm", "critical", "over")
    status_lines: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        normalized = line.casefold()
        if any(word in normalized for word in warnings):
            status_lines.append(line)
        elif any(word in normalized for word in keywords):
            status_lines.append(line)
    return " / ".join(status_lines[:5])


def parsing_reference_cpu_memory(output: str) -> dict[str, str]:
    """Extract CPU and memory values when both appear in one command output."""
    result = {"CPU 사용률": "", "메모리 사용률": ""}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        parts = line.split()
        lower = line.casefold()
        if "cpu" in lower and parts:
            result["CPU 사용률"] = " ".join(parts[-2:])
        if "memory" in lower and parts:
            result["메모리 사용률"] = " ".join(parts[-2:])
    return result

