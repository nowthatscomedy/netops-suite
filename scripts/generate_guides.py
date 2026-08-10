"""Scaffold canonical guides and build the deterministic offline guide bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from scripts.validate_guides import (
        ANCHOR_RE,
        DEFAULT_MANIFEST,
        ID_RE,
        LOCALE_RE,
        RISK_LEVELS,
        ROUTE_RE,
        ValidationResult,
        load_manifest,
        markdown_metadata,
        validate_repository,
    )
except ModuleNotFoundError:  # Direct execution puts scripts/ on sys.path.
    from validate_guides import (  # type: ignore[no-redef]
        ANCHOR_RE,
        DEFAULT_MANIFEST,
        ID_RE,
        LOCALE_RE,
        RISK_LEVELS,
        ROUTE_RE,
        ValidationResult,
        load_manifest,
        markdown_metadata,
        validate_repository,
    )


DEFAULT_OUTPUT = "app/resources/guides"
TEXT_BUNDLE_SUFFIXES = {
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}
DEFAULT_CAPABILITY_SOURCE = "app/assistant/capabilities.py"
DEFAULT_REQUIRED_SECTIONS = [
    "목적",
    "사전 준비 및 권한",
    "입력",
    "단계별 사용법",
    "성공 확인",
    "결과 및 저장 위치",
    "주의사항",
    "오류 해결",
]


class GuideBuildError(RuntimeError):
    """Raised for safe, actionable guide generation failures."""


def _public_capability_records(repo_root: Path) -> list[dict[str, Any]]:
    """Load the pure-Python capability contract without importing the app package."""

    source = repo_root / DEFAULT_CAPABILITY_SOURCE
    if not source.is_file():
        return []
    try:
        namespace = runpy.run_path(str(source))
        factory = namespace.get("all_feature_capabilities")
        capabilities = tuple(factory()) if callable(factory) else ()
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        raise GuideBuildError(f"cannot load assistant capability contract: {exc}") from exc

    records: list[dict[str, Any]] = []
    for index, capability in enumerate(capabilities):
        try:
            support_level = getattr(capability, "support_level")
            risk = getattr(capability, "risk")
            records.append(
                {
                    "feature_id": str(getattr(capability, "feature_id")),
                    "public_name": str(getattr(capability, "public_name")),
                    "guide_id": str(getattr(capability, "guide_id")),
                    "route": str(getattr(capability, "route")),
                    "aliases": list(getattr(capability, "aliases", ())),
                    "intents": list(getattr(capability, "intents", ())),
                    "support_level": str(getattr(support_level, "value", support_level)),
                    "operations": list(getattr(capability, "operations", ())),
                    "inputs": list(getattr(capability, "inputs", ())),
                    "constraints": list(getattr(capability, "constraints", ())),
                    "risk": str(getattr(risk, "value", risk)),
                    "source_paths": list(getattr(capability, "source_paths", ())),
                    "steps": list(getattr(capability, "steps", ())),
                    "success_checks": list(getattr(capability, "success_checks", ())),
                    "stop_conditions": list(getattr(capability, "stop_conditions", ())),
                }
            )
        except Exception as exc:
            raise GuideBuildError(
                f"invalid assistant capability at index {index}: {exc}"
            ) from exc
    return records


def _capabilities_by_guide(
    records: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["guide_id"], []).append(record)
    return grouped


def _risk_for_capabilities(records: Iterable[dict[str, Any]]) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    values = [str(record.get("risk", "low")) for record in records]
    return max(values or ["low"], key=lambda value: order.get(value, 0))


def _merged_strings(*groups: Iterable[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value
            for group in groups
            for item in group
            if (value := str(item or "").strip())
        )
    )


def _safe_repo_path(repo_root: Path, value: str) -> Path:
    if not value or "\\" in value or ":" in value:
        raise GuideBuildError(f"unsafe repository-relative path: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise GuideBuildError(f"unsafe repository-relative path: {value!r}")
    resolved = (repo_root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise GuideBuildError(f"path escapes the repository: {value!r}") from exc
    if resolved == repo_root.resolve():
        raise GuideBuildError("refusing to use the repository root as a generated path")
    return resolved


def _guide_bundle_output_path(repo_root: Path, value: str) -> Path:
    """Resolve the one repository directory owned by the guide generator."""

    output = _safe_repo_path(repo_root, value)
    expected = _safe_repo_path(repo_root, DEFAULT_OUTPUT)
    if output != expected:
        raise GuideBuildError(
            "guide bundle output must be the generator-owned directory "
            f"{DEFAULT_OUTPUT!r}; refusing to replace {value!r}"
        )
    return output


def _is_link_or_reparse_point(path: Path) -> bool:
    """Return whether *path* redirects filesystem traversal."""

    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _validated_bundle_source(source_root: Path, source: Path) -> Path:
    """Resolve one guide asset without following links outside its locale tree."""

    try:
        relative = source.relative_to(source_root)
    except ValueError as exc:
        raise GuideBuildError(f"guide source is outside its locale root: {source}") from exc

    current = source
    while True:
        if _is_link_or_reparse_point(current):
            raise GuideBuildError(
                "guide sources must not contain symbolic links or reparse points: "
                f"{relative.as_posix()}"
            )
        if current == source_root:
            break
        if current.parent == current:
            raise GuideBuildError(f"guide source escapes its locale root: {source}")
        current = current.parent

    try:
        resolved_root = source_root.resolve(strict=True)
        resolved_source = source.resolve(strict=True)
        resolved_source.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise GuideBuildError(
            f"guide source escapes its locale root: {relative.as_posix()}"
        ) from exc
    return resolved_source


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _print_validation_failure(result: ValidationResult) -> None:
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)


def _bundle_path(guide: dict[str, Any]) -> str:
    locale = guide["locale"]
    source_path = PurePosixPath(guide["path"])
    prefix = PurePosixPath("docs") / "user" / locale
    try:
        relative = source_path.relative_to(prefix)
    except ValueError as exc:
        raise GuideBuildError(
            f"guide {guide.get('id')!r} path is outside docs/user/{locale}"
        ) from exc
    return (PurePosixPath("content") / locale / relative).as_posix()


def _markdown_label(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def _build_index(manifest: dict[str, Any], bundled_guides: list[dict[str, Any]]) -> str:
    default_locale = manifest["default_locale"]
    supported_locales = manifest["supported_locales"]
    lines = [
        "# NetOps Suite 사용자 가이드",
        "",
        "이 목차는 `docs/guide_manifest.json`에서 자동 생성됩니다.",
        "",
    ]
    for locale in supported_locales:
        locale_guides = [guide for guide in bundled_guides if guide["locale"] == locale]
        if not locale_guides:
            continue
        heading = f"## {locale}"
        if locale == default_locale:
            heading += " (기본 언어)"
        lines.extend([heading, ""])
        by_parent: dict[str | None, list[dict[str, Any]]] = {}
        locale_ids = {guide["id"] for guide in locale_guides}
        for guide in locale_guides:
            parent = guide.get("parent_id")
            if parent not in locale_ids:
                parent = None
            by_parent.setdefault(parent, []).append(guide)

        def append_children(parent: str | None, depth: int) -> None:
            for guide in by_parent.get(parent, []):
                target = guide["path"]
                if guide.get("anchor"):
                    target += f"#{guide['anchor']}"
                lines.append(
                    f"{'  ' * depth}- [{_markdown_label(guide['title'])}]({target}) "
                    f"`{guide['id']}`"
                )
                append_children(guide["id"], depth + 1)

        append_children(None, 0)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _plain_markdown(markdown: str) -> str:
    markdown = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    markdown = re.sub(r"~~~.*?~~~", " ", markdown, flags=re.DOTALL)
    markdown = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", markdown)
    markdown = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", markdown)
    markdown = re.sub(r"<[^>]+>", " ", markdown)
    markdown = re.sub(r"^\s{0,3}#{1,6}\s+", "", markdown, flags=re.MULTILINE)
    markdown = re.sub(r"[*_`~>|]", " ", markdown)
    markdown = re.sub(r"\s+", " ", markdown)
    return markdown.strip()


def _summary(markdown: str) -> str:
    visible = re.sub(r"<a\s+[^>]*></a>", "", markdown, flags=re.IGNORECASE)
    paragraphs = re.split(r"\n\s*\n", visible)
    for paragraph in paragraphs:
        if paragraph.lstrip().startswith("#"):
            continue
        plain = _plain_markdown(paragraph)
        if plain:
            return plain[:240]
    return ""


def _build_search_index(
    repo_root: Path,
    manifest: dict[str, Any],
    bundled_guides: list[dict[str, Any]],
) -> dict[str, Any]:
    documents: dict[str, str] = {}
    metadata: dict[str, tuple[list[str], set[str]]] = {}
    for guide in bundled_guides:
        canonical_path = guide["canonical_path"]
        if canonical_path not in documents:
            markdown = (repo_root / Path(*PurePosixPath(canonical_path).parts)).read_text(
                encoding="utf-8"
            )
            documents[canonical_path] = markdown
            metadata[canonical_path] = markdown_metadata(markdown)

    entries: list[dict[str, Any]] = []
    for guide in bundled_guides:
        markdown = documents[guide["canonical_path"]]
        headings = metadata[guide["canonical_path"]][0]
        entries.append(
            {
                "id": guide["id"],
                "parent_id": guide.get("parent_id"),
                "title": guide["title"],
                "locale": guide["locale"],
                "path": guide["path"],
                "anchor": guide.get("anchor", ""),
                "route": guide["route"],
                "capability_ids": guide.get("capability_ids", []),
                "keywords": guide["keywords"],
                "risk": guide["risk"],
                "headings": headings,
                "summary": _summary(markdown),
                "text": _plain_markdown(markdown),
            }
        )
    return {
        "schema_version": manifest["schema_version"],
        "default_locale": manifest["default_locale"],
        "entries": entries,
    }


def render_bundle(
    *,
    repo_root: Path,
    manifest_path: str,
    destination: Path,
) -> None:
    validation = validate_repository(repo_root, manifest_path=manifest_path)
    if not validation.ok or validation.manifest is None:
        _print_validation_failure(validation)
        raise GuideBuildError("canonical guides must validate before the bundle is built")
    manifest = validation.manifest
    destination.mkdir(parents=True, exist_ok=True)

    bundled_guides: list[dict[str, Any]] = []
    for source_guide in manifest["guides"]:
        guide = dict(source_guide)
        guide.setdefault("parent_id", None)
        guide.setdefault("anchor", "")
        guide.setdefault("risk", "low")
        guide.setdefault("qa_capture_ids", [])
        canonical_path = guide["path"]
        guide["path"] = _bundle_path(guide)
        bundled_guides.append({**guide, "canonical_path": canonical_path})

    bundle_manifest = dict(manifest)
    bundle_manifest["guides"] = [
        {key: value for key, value in guide.items() if key != "canonical_path"}
        for guide in bundled_guides
    ]
    _write_text(destination / "guide_manifest.json", _json_text(bundle_manifest))
    _write_text(destination / "index.md", _build_index(manifest, bundled_guides))
    search_index = _build_search_index(repo_root, manifest, bundled_guides)
    _write_text(destination / "search_index.json", _json_text(search_index))
    capability_records = _public_capability_records(repo_root)
    if capability_records:
        public_records = [
            {
                key: value
                for key, value in record.items()
                if key != "source_paths"
            }
            for record in capability_records
        ]
        _write_text(
            destination / "capabilities.json",
            _json_text(
                {
                    "schema_version": 1,
                    "default_locale": manifest["default_locale"],
                    "capabilities": public_records,
                }
            ),
        )

    # Copy the complete locale subtree. This preserves Markdown-relative images,
    # capture manifests, and future offline assets without requiring network access.
    for locale in manifest["supported_locales"]:
        source_root = repo_root / "docs" / "user" / locale
        if not source_root.is_dir():
            continue
        target_root = destination / "content" / locale
        for source in sorted(source_root.rglob("*")):
            if _is_link_or_reparse_point(source):
                _validated_bundle_source(source_root, source)
            if not source.is_file():
                continue
            resolved_source = _validated_bundle_source(source_root, source)
            relative = source.relative_to(source_root)
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved_source, target)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if _is_link_or_reparse_point(path):
            _validated_bundle_source(root, path)
        if not path.is_file():
            continue
        resolved = _validated_bundle_source(root, path)
        content = resolved.read_bytes()
        if resolved.suffix.casefold() in TEXT_BUNDLE_SUFFIXES:
            content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        files[path.relative_to(root).as_posix()] = content
    return files


def compare_bundle(expected: Path, actual: Path) -> list[str]:
    expected_files = _tree_bytes(expected)
    actual_files = _tree_bytes(actual)
    differences: list[str] = []
    for path in sorted(expected_files.keys() - actual_files.keys()):
        differences.append(f"missing generated file: {path}")
    for path in sorted(actual_files.keys() - expected_files.keys()):
        differences.append(f"stale generated file: {path}")
    for path in sorted(expected_files.keys() & actual_files.keys()):
        if expected_files[path] != actual_files[path]:
            differences.append(f"out-of-date generated file: {path}")
    return differences


def build_bundle(
    *,
    repo_root: Path,
    manifest_path: str = DEFAULT_MANIFEST,
    output_path: str = DEFAULT_OUTPUT,
    check: bool = False,
) -> int:
    repo_root = repo_root.resolve()
    sync_result = sync_capability_guides(
        repo_root=repo_root,
        manifest_path=manifest_path,
        check=check,
    )
    if sync_result:
        return sync_result
    output = _guide_bundle_output_path(repo_root, output_path)
    with tempfile.TemporaryDirectory(prefix="netops-guides-") as temporary:
        expected = Path(temporary) / "guides"
        render_bundle(
            repo_root=repo_root,
            manifest_path=manifest_path,
            destination=expected,
        )
        if check:
            differences = compare_bundle(expected, output)
            if differences:
                for difference in differences:
                    print(f"ERROR: {difference}", file=sys.stderr)
                print(
                    "Guide bundle is stale. Run: python scripts/generate_guides.py build",
                    file=sys.stderr,
                )
                return 1
            print(f"Guide bundle is current ({len(_tree_bytes(expected))} files).")
            return 0

        if output.exists():
            shutil.rmtree(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(expected, output)
    print(f"Built offline guide bundle: {output.relative_to(repo_root).as_posix()}")
    return 0


def _new_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "default_locale": "ko",
        "supported_locales": ["ko"],
        "required_sections": DEFAULT_REQUIRED_SECTIONS,
        "guides": [],
    }


def _scaffold_markdown(title: str, anchor: str, required_sections: list[str]) -> str:
    lines: list[str] = []
    if anchor:
        lines.extend([f'<a id="{anchor}"></a>', ""])
    lines.extend([f"# {title}", ""])
    for section in required_sections:
        lines.extend([f"## {section}", "", "TODO: 내용을 작성하세요.", ""])
    return "\n".join(lines).rstrip() + "\n"


def sync_capability_guides(
    *,
    repo_root: Path,
    manifest_path: str = DEFAULT_MANIFEST,
    check: bool = False,
) -> int:
    """Synchronize guide metadata and scaffold guides for registered capabilities.

    The canonical Markdown remains author-owned.  Machine-verifiable capability
    facts are emitted to the offline bundle while this function only updates the
    manifest metadata and creates a non-overwriting guide draft for a new guide.
    """

    repo_root = repo_root.resolve()
    records = _public_capability_records(repo_root)
    if not records:
        return 0
    resolved_manifest = _safe_repo_path(repo_root, manifest_path)
    try:
        manifest = load_manifest(resolved_manifest)
    except ValueError as exc:
        raise GuideBuildError(str(exc)) from exc
    guides = manifest.get("guides")
    if not isinstance(guides, list):
        raise GuideBuildError("manifest guides must be an array")
    by_id = {
        guide.get("id"): guide
        for guide in guides
        if isinstance(guide, dict) and isinstance(guide.get("id"), str)
    }
    default_locale = str(manifest.get("default_locale") or "ko")
    required_sections = manifest.get("required_sections")
    if not isinstance(required_sections, list) or not all(
        isinstance(value, str) and value for value in required_sections
    ):
        raise GuideBuildError("manifest required_sections must be a non-empty string array")

    changes: list[str] = []
    created_docs: list[tuple[Path, str]] = []
    for guide_id, linked in _capabilities_by_guide(records).items():
        primary = next(
            (item for item in linked if item["feature_id"] == guide_id),
            linked[0],
        )
        source_paths = _merged_strings(
            *(item.get("source_paths", []) for item in linked)
        )
        keywords = _merged_strings(
            *(item.get("aliases", []) for item in linked),
            *(item.get("intents", []) for item in linked),
            (item["public_name"] for item in linked),
        )
        capability_ids = [item["feature_id"] for item in linked]
        guide = by_id.get(guide_id)
        if guide is None:
            path_value = f"docs/user/{default_locale}/{guide_id.replace('.', '/')}.md"
            anchor = guide_id.replace(".", "-")
            guide = {
                "id": guide_id,
                "parent_id": None if guide_id == "getting-started" else "getting-started",
                "title": primary["public_name"],
                "locale": default_locale,
                "path": path_value,
                "anchor": anchor,
                "route": primary["route"],
                "source_paths": source_paths or [DEFAULT_CAPABILITY_SOURCE],
                "keywords": keywords or [primary["public_name"]],
                "risk": _risk_for_capabilities(linked),
                "qa_capture_ids": [],
                "capability_ids": capability_ids,
            }
            guides.append(guide)
            by_id[guide_id] = guide
            doc_path = _safe_repo_path(repo_root, path_value)
            created_docs.append(
                (doc_path, _scaffold_markdown(primary["public_name"], anchor, required_sections))
            )
            changes.append(f"create guide {guide_id}: {path_value}")
            continue

        desired = {
            "title": primary["public_name"],
            "route": primary["route"],
            "risk": _risk_for_capabilities(linked),
            "source_paths": _merged_strings(guide.get("source_paths", []), source_paths),
            "keywords": _merged_strings(guide.get("keywords", []), keywords),
            "capability_ids": capability_ids,
        }
        for key, value in desired.items():
            if guide.get(key) == value:
                continue
            guide[key] = value
            changes.append(f"update guide {guide_id}: {key}")

    if not changes:
        return 0
    if check:
        for change in changes:
            print(f"ERROR: capability guide contract is stale: {change}", file=sys.stderr)
        print(
            "Run: python scripts/generate_guides.py sync-capabilities",
            file=sys.stderr,
        )
        return 1

    temporary_manifest = resolved_manifest.with_suffix(resolved_manifest.suffix + ".tmp")
    newly_created: list[Path] = []
    try:
        temporary_manifest.parent.mkdir(parents=True, exist_ok=True)
        _write_text(temporary_manifest, _json_text(manifest))
        for path, content in created_docs:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            newly_created.append(path)
        os.replace(temporary_manifest, resolved_manifest)
    except Exception:
        temporary_manifest.unlink(missing_ok=True)
        for path in newly_created:
            path.unlink(missing_ok=True)
        raise
    for change in changes:
        print(f"Synchronized capability guide: {change}")
    return 0


def scaffold_guide(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    manifest_path = _safe_repo_path(repo_root, args.manifest)
    if manifest_path.exists():
        try:
            manifest = load_manifest(manifest_path)
        except ValueError as exc:
            raise GuideBuildError(str(exc)) from exc
    else:
        manifest = _new_manifest()

    if not ID_RE.fullmatch(args.id):
        raise GuideBuildError("guide ID must be a lowercase dotted identifier")
    guides = manifest.get("guides")
    if not isinstance(guides, list):
        raise GuideBuildError("manifest guides must be an array")
    if any(isinstance(guide, dict) and guide.get("id") == args.id for guide in guides):
        raise GuideBuildError(f"guide ID already exists: {args.id}")
    supported_locales = manifest.get("supported_locales", [])
    if args.locale not in supported_locales or not LOCALE_RE.fullmatch(args.locale):
        raise GuideBuildError(
            f"locale {args.locale!r} is not listed in manifest supported_locales"
        )
    if args.parent_id and not any(
        isinstance(guide, dict) and guide.get("id") == args.parent_id for guide in guides
    ):
        raise GuideBuildError(f"parent guide does not exist: {args.parent_id}")
    if not ROUTE_RE.fullmatch(args.route):
        raise GuideBuildError("route must match [a-z0-9.-]+(?::[a-z0-9.-]+)*")
    if args.anchor and not ANCHOR_RE.fullmatch(args.anchor):
        raise GuideBuildError("anchor must be stable lowercase ASCII kebab-case")
    if any(
        isinstance(guide, dict)
        and guide.get("locale", manifest.get("default_locale")) == args.locale
        and guide.get("route") == args.route
        for guide in guides
    ):
        raise GuideBuildError(f"guide route already exists for {args.locale}: {args.route}")

    path_value = args.path or (
        f"docs/user/{args.locale}/{args.id.replace('.', '/')}.md"
    )
    expected_prefix = f"docs/user/{args.locale}/"
    if not path_value.startswith(expected_prefix):
        raise GuideBuildError(f"guide path must stay below {expected_prefix}")
    doc_path = _safe_repo_path(repo_root, path_value)
    if doc_path.exists():
        raise GuideBuildError(f"refusing to overwrite existing guide: {path_value}")
    if manifest_path.with_suffix(manifest_path.suffix + ".tmp").exists():
        raise GuideBuildError("manifest temporary file already exists; inspect it before retrying")

    required_sections = manifest.get("required_sections")
    if not isinstance(required_sections, list) or not all(
        isinstance(value, str) and value for value in required_sections
    ):
        raise GuideBuildError("manifest required_sections must be a non-empty string array")
    source_paths = list(dict.fromkeys(args.source_path))
    keywords = list(dict.fromkeys(args.keyword or [args.title]))
    capture_ids = list(dict.fromkeys(args.qa_capture_id))
    new_guide = {
        "id": args.id,
        "parent_id": args.parent_id,
        "title": args.title.strip(),
        "locale": args.locale,
        "path": path_value,
        "anchor": args.anchor,
        "route": args.route,
        "source_paths": source_paths,
        "keywords": keywords,
        "risk": args.risk,
        "qa_capture_ids": capture_ids,
    }
    guides.append(new_guide)

    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    doc_created = False
    try:
        temporary_manifest.parent.mkdir(parents=True, exist_ok=True)
        _write_text(temporary_manifest, _json_text(manifest))
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        with doc_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_scaffold_markdown(args.title.strip(), args.anchor, required_sections))
        doc_created = True
        os.replace(temporary_manifest, manifest_path)
    except Exception:
        temporary_manifest.unlink(missing_ok=True)
        if doc_created:
            doc_path.unlink(missing_ok=True)
        raise

    print(f"Scaffolded {args.id}: {path_value}")
    print("Complete every TODO, add/verify a QA capture when needed, then run:")
    print("  python scripts/generate_guides.py build")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build the offline guide bundle")
    build.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )
    build.add_argument("--manifest", default=DEFAULT_MANIFEST)
    build.add_argument("--output", default=DEFAULT_OUTPUT)
    build.add_argument(
        "--check",
        action="store_true",
        help="compare a temporary render with committed generated files",
    )

    sync = subparsers.add_parser(
        "sync-capabilities",
        help="sync the public feature capability contract with guide metadata",
    )
    sync.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )
    sync.add_argument("--manifest", default=DEFAULT_MANIFEST)
    sync.add_argument(
        "--check",
        action="store_true",
        help="report stale capability/guide mappings without writing files",
    )

    scaffold = subparsers.add_parser(
        "scaffold", help="create a manifest entry and non-overwriting guide draft"
    )
    scaffold.add_argument("id")
    scaffold.add_argument("--title", required=True)
    scaffold.add_argument("--parent-id")
    scaffold.add_argument("--locale", default="ko")
    scaffold.add_argument("--path")
    scaffold.add_argument("--anchor", default="")
    scaffold.add_argument("--route")
    scaffold.add_argument("--source-path", action="append", required=True)
    scaffold.add_argument("--keyword", action="append", default=[])
    scaffold.add_argument("--risk", choices=sorted(RISK_LEVELS), default="low")
    scaffold.add_argument("--qa-capture-id", action="append", default=[])
    scaffold.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[1])
    )
    scaffold.add_argument("--manifest", default=DEFAULT_MANIFEST)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    # Convenience alias retained for simple CI/local use.
    if arguments == ["--check"]:
        arguments = ["build", "--check"]
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        if args.command == "build":
            return build_bundle(
                repo_root=Path(args.repo_root),
                manifest_path=args.manifest,
                output_path=args.output,
                check=args.check,
            )
        if args.command == "sync-capabilities":
            return sync_capability_guides(
                repo_root=Path(args.repo_root),
                manifest_path=args.manifest,
                check=args.check,
            )
        if args.route is None:
            args.route = args.id.replace(".", ":")
        return scaffold_guide(args)
    except (GuideBuildError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
