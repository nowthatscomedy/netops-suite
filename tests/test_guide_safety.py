from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.generate_guides import (
    GuideBuildError,
    _validated_bundle_source,
    build_bundle,
)
from scripts.validate_guides import _git_changed_paths


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


@pytest.mark.parametrize("unsafe_output", ["app", "docs", "app/resources"])
def test_guide_build_refuses_to_replace_non_owned_repository_directories(
    tmp_path: Path,
    unsafe_output: str,
) -> None:
    protected = tmp_path / unsafe_output / "keep.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("user-owned\n", encoding="utf-8")

    with pytest.raises(GuideBuildError, match="generator-owned directory"):
        build_bundle(repo_root=tmp_path, output_path=unsafe_output)

    assert protected.read_text(encoding="utf-8") == "user-owned\n"


def test_guide_bundle_source_rejects_file_symlink_outside_locale_root(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "docs" / "user" / "ko"
    source_root.mkdir(parents=True)
    outside = tmp_path / "release-host-secret.txt"
    outside.write_text("must-not-enter-public-bundle\n", encoding="utf-8")
    linked_asset = source_root / "linked-asset.txt"
    try:
        linked_asset.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    with pytest.raises(GuideBuildError, match="symbolic links or reparse points"):
        _validated_bundle_source(source_root, linked_asset)


def test_guide_bundle_source_accepts_regular_file_inside_locale_root(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "docs" / "user" / "ko"
    source = source_root / "assets" / "guide.txt"
    source.parent.mkdir(parents=True)
    source.write_text("offline guide asset\n", encoding="utf-8")

    assert _validated_bundle_source(source_root, source) == source.resolve()


def test_git_changed_paths_includes_deletions_and_both_rename_paths(
    tmp_path: Path,
) -> None:
    _run_git(tmp_path, "init", "--initial-branch=main")
    _run_git(tmp_path, "config", "user.name", "NetOps Suite Tests")
    _run_git(tmp_path, "config", "user.email", "tests@example.invalid")

    source_dir = tmp_path / "app" / "ui"
    source_dir.mkdir(parents=True)
    deleted = source_dir / "deleted_feature.py"
    renamed_from = source_dir / "old_feature.py"
    deleted.write_text("DELETED = True\n", encoding="utf-8")
    renamed_from.write_text("RENAMED = True\n", encoding="utf-8")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", "base")
    base = _run_git(tmp_path, "rev-parse", "HEAD")

    deleted.unlink()
    renamed_from.rename(source_dir / "new_feature.py")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-m", "delete and rename")

    assert _git_changed_paths(tmp_path, base, "HEAD") == [
        "app/ui/deleted_feature.py",
        "app/ui/new_feature.py",
        "app/ui/old_feature.py",
    ]
