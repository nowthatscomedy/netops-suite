from __future__ import annotations

from pathlib import Path


def test_app_owned_text_uses_profile_not_legacy_wording():
    bad_terms = ("tem" + "plate", "Tem" + "plate", "TEM" + "PLATE", "템" + "플릿")
    allowed_markers = ("Tem" + "plateSyntaxError", "ntc_" + "tem" + "plates")
    roots = [Path("app"), Path("netops_suite"), Path("tests")]
    suffixes = {".py", ".md", ".yaml", ".yml", ".txt", ".spec"}
    violations: list[str] = []

    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if any(marker in line for marker in allowed_markers):
                    continue
                if any(term in line for term in bad_terms):
                    violations.append(f"{path}:{line_number}: {line.strip()}")

    assert violations == []
