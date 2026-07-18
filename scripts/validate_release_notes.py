from __future__ import annotations

import argparse
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


TAG_PATTERN = re.compile(
    r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
MAX_RELEASE_NOTES_BYTES = 100 * 1024
MIN_RELEASE_NOTES_CHARACTERS = 500
REQUIRED_HEADINGS = (
    "한눈에 보기",
    "이전 버전과 달라진 점",
    "새 기능",
    "개선 및 수정",
    "설치 및 주의사항",
    "검증 및 무결성",
    "전체 변경 내역",
)
PLACEHOLDER_PATTERN = re.compile(
    r"(?im)\b(?:TODO|TBD)\b|작성\s*예정|내용을\s*작성"
)


class ReleaseNotesValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseNotesDocument:
    tag: str
    path: Path
    body: str


def _is_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return path.is_symlink()
    attributes = getattr(
        path.stat(follow_symlinks=False),
        "st_file_attributes",
        0,
    )
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _section_contents(body: str) -> dict[str, str]:
    headings = list(re.finditer(r"(?m)^## ([^\r\n]+)\s*$", body))
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        sections[match.group(1).strip()] = body[match.end() : end].strip()
    return sections


def load_release_notes(project_root: Path, tag: str) -> ReleaseNotesDocument:
    normalized_tag = str(tag or "").strip()
    if not TAG_PATTERN.fullmatch(normalized_tag):
        raise ReleaseNotesValidationError(
            "Release tag must be a semantic version such as v1.0.0."
        )

    root = Path(project_root).resolve(strict=True)
    notes_root = root / "release-notes"
    if not notes_root.is_dir() or _is_reparse_point(notes_root):
        raise ReleaseNotesValidationError(
            f"Release notes directory is missing or unsafe: {notes_root}"
        )

    requested_path = notes_root / f"{normalized_tag}.md"
    if not requested_path.is_file() or _is_reparse_point(requested_path):
        raise ReleaseNotesValidationError(
            f"Release notes file is missing or unsafe: {requested_path}"
        )
    resolved_path = requested_path.resolve(strict=True)
    if resolved_path.parent != notes_root.resolve(strict=True):
        raise ReleaseNotesValidationError(
            f"Release notes must stay inside {notes_root}."
        )

    raw = resolved_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ReleaseNotesValidationError(
            "Release notes must be UTF-8 without a byte-order mark."
        )
    if not raw or len(raw) > MAX_RELEASE_NOTES_BYTES:
        raise ReleaseNotesValidationError(
            "Release notes must be non-empty and no larger than 100 KiB."
        )
    try:
        body = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReleaseNotesValidationError(
            "Release notes must be valid UTF-8."
        ) from exc
    if "\x00" in body:
        raise ReleaseNotesValidationError("Release notes must not contain NUL bytes.")

    body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(body) < MIN_RELEASE_NOTES_CHARACTERS:
        raise ReleaseNotesValidationError(
            "Release notes are too short to be a detailed user-facing summary."
        )
    first_nonempty_line = next(
        (line.strip() for line in body.splitlines() if line.strip()),
        "",
    )
    expected_title = f"# NetOps Suite {normalized_tag}"
    if first_nonempty_line != expected_title:
        raise ReleaseNotesValidationError(
            f"Release notes must start with: {expected_title}"
        )
    if PLACEHOLDER_PATTERN.search(body):
        raise ReleaseNotesValidationError(
            "Release notes still contain placeholder text."
        )

    sections = _section_contents(body)
    missing = [
        heading
        for heading in REQUIRED_HEADINGS
        if not sections.get(heading, "").strip()
    ]
    if missing:
        raise ReleaseNotesValidationError(
            "Release notes have missing or empty sections: " + ", ".join(missing)
        )

    return ReleaseNotesDocument(
        tag=normalized_tag,
        path=resolved_path,
        body=body,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a versioned NetOps Suite GitHub release note."
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)

    try:
        document = load_release_notes(args.project_root, args.tag)
    except (OSError, ReleaseNotesValidationError) as exc:
        parser.error(str(exc))
    print(
        f"Validated release notes for {document.tag}: "
        f"{document.path} ({len(document.body)} characters)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
