from __future__ import annotations

from pathlib import Path

import pytest

from app.version import __version__
from scripts.validate_release_notes import (
    MAX_RELEASE_NOTES_BYTES,
    REQUIRED_HEADINGS,
    ReleaseNotesValidationError,
    load_release_notes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _valid_notes(tag: str = "v9.8.7") -> str:
    introduction = (
        "이 버전은 사용자가 이전 버전과 달라진 내용을 쉽게 확인하도록 작성한 "
        "검증용 상세 릴리즈 노트입니다. "
    )
    sections = []
    for heading in REQUIRED_HEADINGS:
        sections.append(
            f"## {heading}\n\n"
            f"- {heading}에 해당하는 실제 변경과 사용자 영향을 구체적으로 설명합니다.\n"
            f"- 검증 가능한 정보와 사용 시 주의할 내용을 함께 기록합니다."
        )
    return (
        f"# NetOps Suite {tag}\n\n"
        f"{introduction * 3}\n\n"
        + "\n\n".join(sections)
    )


def _write_notes(
    root: Path,
    *,
    tag: str = "v9.8.7",
    body: str | None = None,
    raw: bytes | None = None,
) -> Path:
    notes_root = root / "release-notes"
    notes_root.mkdir(parents=True)
    path = notes_root / f"{tag}.md"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(body or _valid_notes(tag), encoding="utf-8")
    return path


def test_current_version_has_valid_detailed_release_notes():
    tag = f"v{__version__}"
    document = load_release_notes(PROJECT_ROOT, tag)

    assert document.path == (PROJECT_ROOT / "release-notes" / f"{tag}.md").resolve()
    assert document.body.startswith(f"# NetOps Suite {tag}\n")
    assert "이전 버전과 달라진 점" in document.body
    assert "새 기능" in document.body


def test_v1_0_9_release_notes_reference_the_published_installer_hash():
    document = load_release_notes(PROJECT_ROOT, "v1.0.9")

    assert "683e73fabb9558f289092622c96aa680c67c488a72aab8c62b1abd05537b8cd8" in document.body


def test_all_versioned_release_notes_follow_the_contract():
    versioned_notes = sorted((PROJECT_ROOT / "release-notes").glob("v*.md"))

    assert versioned_notes
    for path in versioned_notes:
        document = load_release_notes(PROJECT_ROOT, path.stem)
        assert document.path == path.resolve()


def test_release_notes_normalize_line_endings_and_preserve_shell_like_text(tmp_path):
    tag = "v9.8.7"
    shell_like_text = (
        "\n- 리터럴 검증: `$env:GITHUB_TOKEN`, `$(throw 'boom')`, "
        "`${{ github.token }}`"
    )
    body = (_valid_notes(tag) + shell_like_text).replace("\n", "\r\n")
    _write_notes(tmp_path, tag=tag, body=body)

    document = load_release_notes(tmp_path, tag)

    assert "\r" not in document.body
    assert "$env:GITHUB_TOKEN" in document.body
    assert "$(throw 'boom')" in document.body
    assert "${{ github.token }}" in document.body


@pytest.mark.parametrize(
    ("body_transform", "message"),
    [
        (
            lambda body: body.replace(
                "# NetOps Suite v9.8.7",
                "# NetOps Suite v9.8.6",
            ),
            "must start with",
        ),
        (
            lambda body: body.replace("## 새 기능", "## 다른 섹션"),
            "missing or empty sections",
        ),
        (
            lambda body: body + "\n\nTODO: 나중에 작성",
            "placeholder",
        ),
        (
            lambda _body: "# NetOps Suite v9.8.7\n\n너무 짧습니다.",
            "too short",
        ),
    ],
)
def test_release_notes_reject_incomplete_content(
    tmp_path,
    body_transform,
    message,
):
    tag = "v9.8.7"
    _write_notes(
        tmp_path,
        tag=tag,
        body=body_transform(_valid_notes(tag)),
    )

    with pytest.raises(ReleaseNotesValidationError, match=message):
        load_release_notes(tmp_path, tag)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"\xef\xbb\xbf" + _valid_notes().encode("utf-8"), "byte-order mark"),
        (b"\xff\xfe\x00\x00", "valid UTF-8"),
        (_valid_notes().encode("utf-8") + b"\x00", "NUL"),
        (b"x" * (MAX_RELEASE_NOTES_BYTES + 1), "no larger than"),
    ],
    ids=("utf8-bom", "invalid-utf8", "nul-byte", "oversized"),
)
def test_release_notes_reject_unsafe_encoding_or_size(tmp_path, raw, message):
    _write_notes(tmp_path, raw=raw)

    with pytest.raises(ReleaseNotesValidationError, match=message):
        load_release_notes(tmp_path, "v9.8.7")


def test_release_notes_reject_invalid_tag_and_missing_version_file(tmp_path):
    (tmp_path / "release-notes").mkdir()

    with pytest.raises(ReleaseNotesValidationError, match="semantic version"):
        load_release_notes(tmp_path, "../outside")
    with pytest.raises(ReleaseNotesValidationError, match="missing or unsafe"):
        load_release_notes(tmp_path, "v9.8.7")
