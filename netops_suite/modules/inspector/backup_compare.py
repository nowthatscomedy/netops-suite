"""Compare a backup run with each device's previous backup."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

# Lines that change on every backup without a configuration change.
_VOLATILE_LINE_PATTERNS = (
    re.compile(r"^\s*!\s*(last configuration change|nvram config last updated|no configuration change since)", re.IGNORECASE),
    re.compile(r"^\s*!\s*time:", re.IGNORECASE),
    re.compile(r"^\s*(current configuration\s*:|building configuration)", re.IGNORECASE),
    re.compile(r"^\s*ntp clock-period\b", re.IGNORECASE),
)

BackupStatus = str  # "changed" | "same" | "new" | "missing"


@dataclass(frozen=True, slots=True)
class BackupComparison:
    file_name: str
    current_path: Path | None
    previous_path: Path | None
    status: BackupStatus
    added: int = 0
    removed: int = 0

    @property
    def label(self) -> str:
        if self.status == "changed":
            return f"변경됨 (+{self.added} / -{self.removed}줄)"
        if self.status == "same":
            return "이전과 같음"
        if self.status == "new":
            return "첫 백업"
        return "백업 파일 없음"


def comparable_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if any(pattern.search(line) for pattern in _VOLATILE_LINE_PATTERNS):
            continue
        lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def find_previous_backup(current_dir: Path, file_name: str) -> Path | None:
    """Newest earlier run folder that holds a backup with the same name."""
    root = current_dir.parent
    if not root.is_dir():
        return None
    earlier = sorted(
        (
            entry
            for entry in root.iterdir()
            if entry.is_dir() and entry.name < current_dir.name
        ),
        key=lambda entry: entry.name,
        reverse=True,
    )
    for folder in earlier:
        candidate = folder / file_name
        if candidate.is_file():
            return candidate
    return None


def compare_backup_file(current_path: Path) -> BackupComparison:
    file_name = current_path.name
    if not current_path.is_file():
        return BackupComparison(file_name, None, None, "missing")
    previous_path = find_previous_backup(current_path.parent, file_name)
    if previous_path is None:
        return BackupComparison(file_name, current_path, None, "new")
    current = comparable_lines(_read_text(current_path))
    previous = comparable_lines(_read_text(previous_path))
    if current == previous:
        return BackupComparison(file_name, current_path, previous_path, "same")
    added = removed = 0
    for line in difflib.ndiff(previous, current):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return BackupComparison(
        file_name, current_path, previous_path, "changed", added, removed
    )


def backup_diff_lines(comparison: BackupComparison) -> list[str]:
    """Unified diff from the previous backup to this one."""
    if comparison.current_path is None or comparison.previous_path is None:
        return []
    previous = comparable_lines(_read_text(comparison.previous_path))
    current = comparable_lines(_read_text(comparison.current_path))
    return list(
        difflib.unified_diff(
            previous,
            current,
            fromfile=f"이전 백업 ({comparison.previous_path.parent.name})",
            tofile=f"이번 백업 ({comparison.current_path.parent.name})",
            lineterm="",
            n=3,
        )
    )


def backup_file_for_result(
    result: dict, backup_dir: Path, work_dir: Path | None = None
) -> Path:
    """Where a result row's backup was written."""
    recorded = str(result.get("backup_file") or "").strip()
    if recorded:
        path = Path(recorded)
        if not path.is_absolute() and work_dir is not None:
            path = work_dir / path
        if path.is_file():
            return path
        return backup_dir / path.name
    name = f"{result.get('ip', '')}_{result.get('vendor', '')}_{result.get('os', '')}.txt"
    return backup_dir / name
