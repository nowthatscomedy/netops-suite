"""Validate the canonical NetOps Suite user-guide catalog and PR impact.

The validator intentionally uses only the Python standard library so it can run
before project dependencies are installed.  Structural validation is always
enforced.  The ``guide-not-required`` escape hatch applies only to the optional
Git-diff impact check and never hides malformed or stale documentation.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

try:
    from scripts.guide_capture_fingerprint import capture_manifest_errors
except ModuleNotFoundError:  # Direct execution puts scripts/ on sys.path.
    from guide_capture_fingerprint import capture_manifest_errors  # type: ignore[no-redef]


SCHEMA_MAJOR = 1
DEFAULT_MANIFEST = "docs/guide_manifest.json"
GUIDE_ROOT = "docs/user"
GENERATED_ROOT = "app/resources/guides"
GUIDE_CAPTURE_CONFIG = "qa/offscreen/guide_scenarios.json"
GUIDE_CAPTURE_MANIFEST = "docs/user/ko/assets/generated/capture-manifest.json"
RISK_LEVELS = {"low", "medium", "high"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$")
ROUTE_RE = re.compile(r"^[a-z0-9.-]+(?::[a-z0-9.-]+)*$")
ANCHOR_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
QA_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
EXPLICIT_ANCHOR_RE = re.compile(
    r"<(?:a\s+(?:[^>]*?\s)?(?:id|name)|[^>]+\sid)=[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE),
    re.compile(r"\{\{\s*(?:WRITE_GUIDE_HERE|GUIDE_CONTENT_REQUIRED)\s*\}\}", re.IGNORECASE),
    re.compile(r"\[(?:작성|내용)\s*(?:필요|예정)?\]"),
    re.compile(r"(?:여기에\s+작성|추후\s+작성|작성\s+필요|lorem\s+ipsum)", re.IGNORECASE),
)
USER_FACING_PREFIXES = (
    "app/main_window.py",
    "app/ui/",
    "app/services/",
    "netops_suite/modules/",
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] | None = None
    guides: list[dict[str, Any]] = field(default_factory=list)
    affected_ids: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"manifest not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc

    try:
        value = json.loads(raw, object_pairs_hook=_json_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("guide manifest root must be a JSON object")
    return value


def _schema_major(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)*", value):
        return int(value.split(".", 1)[0])
    return None


def _safe_repo_path(repo_root: Path, value: str) -> Path | None:
    if not value or "\\" in value or ":" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    resolved = (repo_root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return resolved


def _as_string_list(
    value: Any,
    *,
    label: str,
    result: ValidationResult,
    allow_empty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        result.error(f"{label} must be an array of strings")
        return []
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            result.error(f"{label}[{index}] must be a non-empty string")
            continue
        strings.append(item.strip())
    if not allow_empty and not strings:
        result.error(f"{label} must contain at least one value")
    if len(strings) != len(set(strings)):
        result.error(f"{label} contains duplicate values")
    return strings


def _strip_fenced_code(markdown: str) -> str:
    output: list[str] = []
    in_fence = False
    marker = ""
    for line in markdown.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            current = match.group(1)[0]
            if not in_fence:
                in_fence = True
                marker = current
            elif current == marker:
                in_fence = False
                marker = ""
            output.append("")
            continue
        output.append("" if in_fence else line)
    return "\n".join(output)


def _clean_heading(value: str) -> str:
    value = re.sub(r"\s*\{#[a-z0-9-]+\}\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+#+\s*$", "", value)
    value = re.sub(r"[*_`~]", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _github_slug(value: str) -> str:
    value = _clean_heading(value).lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value


def markdown_metadata(markdown: str) -> tuple[list[str], set[str]]:
    visible = _strip_fenced_code(markdown)
    headings: list[str] = []
    anchors = set(EXPLICIT_ANCHOR_RE.findall(visible))
    slug_counts: dict[str, int] = {}
    for line in visible.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        raw_heading = match.group(2)
        heading = _clean_heading(raw_heading)
        if heading:
            headings.append(heading)
        explicit = re.search(r"\{#([a-z0-9-]+)\}\s*$", raw_heading, re.IGNORECASE)
        if explicit:
            anchors.add(explicit.group(1))
        slug = _github_slug(raw_heading)
        if slug:
            count = slug_counts.get(slug, 0)
            anchors.add(slug if count == 0 else f"{slug}-{count}")
            slug_counts[slug] = count + 1
    return headings, anchors


def _normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_heading(value)).strip().rstrip(":：")


def _link_destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    # Markdown titles follow the URL after whitespace. Paths in this repository
    # intentionally do not contain unescaped spaces.
    return value.split(maxsplit=1)[0] if value else ""


def _validate_links(
    *,
    repo_root: Path,
    doc_path: Path,
    markdown: str,
    result: ValidationResult,
    metadata_cache: dict[Path, tuple[list[str], set[str]]],
) -> None:
    visible = _strip_fenced_code(markdown)
    for raw_target in MARKDOWN_LINK_RE.findall(visible):
        destination = _link_destination(raw_target)
        if not destination:
            result.error(f"{doc_path.relative_to(repo_root).as_posix()}: empty Markdown link")
            continue
        split = urlsplit(destination)
        if split.scheme or split.netloc:
            continue
        raw_path = unquote(split.path)
        fragment = unquote(split.fragment)
        if not raw_path:
            target_path = doc_path
        elif raw_path.startswith("/"):
            target_path = _safe_repo_path(repo_root, raw_path.lstrip("/"))
        else:
            target_path = (doc_path.parent / Path(*PurePosixPath(raw_path).parts)).resolve()
            try:
                target_path.relative_to(repo_root.resolve())
            except ValueError:
                target_path = None
        display = doc_path.relative_to(repo_root).as_posix()
        if target_path is None:
            result.error(f"{display}: link escapes the repository: {destination}")
            continue
        if not target_path.exists():
            result.error(f"{display}: broken local link or asset: {destination}")
            continue
        if fragment and target_path.suffix.lower() in {".md", ".markdown"}:
            if target_path not in metadata_cache:
                try:
                    target_markdown = target_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    result.error(f"{display}: cannot inspect linked document {destination}: {exc}")
                    continue
                metadata_cache[target_path] = markdown_metadata(target_markdown)
            if fragment not in metadata_cache[target_path][1]:
                result.error(f"{display}: unknown link anchor: {destination}")


def _source_pattern_matches(repo_root: Path, pattern: str) -> bool:
    if any(character in pattern for character in "*?["):
        try:
            return any(repo_root.glob(pattern))
        except (OSError, ValueError):
            return False
    path = _safe_repo_path(repo_root, pattern.rstrip("/"))
    return path is not None and path.exists()


def _load_qa_ids(repo_root: Path, result: ValidationResult) -> set[str] | None:
    scenarios_path = repo_root / "qa" / "offscreen" / "scenarios.json"
    if not scenarios_path.exists():
        result.warn("qa/offscreen/scenarios.json is absent; QA capture IDs were not cross-checked")
        return None
    try:
        data = json.loads(scenarios_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result.error(f"cannot read QA scenarios: {exc}")
        return set()
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(scenarios, list):
        result.error("qa/offscreen/scenarios.json: scenarios must be an array")
        return set()
    identifiers: set[str] = set()
    for index, scenario in enumerate(scenarios):
        identifier = scenario.get("id") if isinstance(scenario, dict) else None
        if not isinstance(identifier, str) or not identifier:
            result.error(f"qa/offscreen/scenarios.json: scenarios[{index}].id is invalid")
            continue
        if identifier in identifiers:
            result.error(f"qa/offscreen/scenarios.json: duplicate scenario ID {identifier!r}")
        identifiers.add(identifier)
    return identifiers


def _validate_parent_graph(
    guides_by_id: dict[str, dict[str, Any]], result: ValidationResult
) -> None:
    states: dict[str, int] = {}

    def visit(identifier: str, stack: list[str]) -> None:
        state = states.get(identifier, 0)
        if state == 2:
            return
        if state == 1:
            cycle_start = stack.index(identifier) if identifier in stack else 0
            cycle = stack[cycle_start:] + [identifier]
            result.error(f"guide parent cycle: {' -> '.join(cycle)}")
            return
        states[identifier] = 1
        parent = guides_by_id[identifier].get("parent_id")
        if isinstance(parent, str) and parent in guides_by_id:
            visit(parent, stack + [identifier])
        states[identifier] = 2

    for identifier in guides_by_id:
        visit(identifier, [])


def _validate_manifest_structure(
    repo_root: Path, manifest: dict[str, Any], result: ValidationResult
) -> None:
    major = _schema_major(manifest.get("schema_version"))
    if major != SCHEMA_MAJOR:
        result.error(
            f"schema_version must have supported major version {SCHEMA_MAJOR}; "
            f"received {manifest.get('schema_version')!r}"
        )

    default_locale = manifest.get("default_locale")
    if not isinstance(default_locale, str) or not LOCALE_RE.fullmatch(default_locale):
        result.error("default_locale must be a valid locale string")
        default_locale = ""

    supported_locales = _as_string_list(
        manifest.get("supported_locales"),
        label="supported_locales",
        result=result,
    )
    for locale in supported_locales:
        if not LOCALE_RE.fullmatch(locale):
            result.error(f"supported_locales contains invalid locale {locale!r}")
    if default_locale and default_locale not in supported_locales:
        result.error("default_locale must be present in supported_locales")

    required_sections = _as_string_list(
        manifest.get("required_sections"),
        label="required_sections",
        result=result,
    )
    guides_value = manifest.get("guides")
    if not isinstance(guides_value, list):
        result.error("guides must be an array")
        return
    if not guides_value:
        result.error("guides must contain at least one guide")
        return

    qa_ids = _load_qa_ids(repo_root, result)
    guides_by_id: dict[str, dict[str, Any]] = {}
    route_owners: dict[tuple[str, str], str] = {}
    anchor_owners: dict[tuple[str, str], str] = {}
    canonical_docs: set[Path] = set()
    metadata_cache: dict[Path, tuple[list[str], set[str]]] = {}

    for index, guide_value in enumerate(guides_value):
        label = f"guides[{index}]"
        if not isinstance(guide_value, dict):
            result.error(f"{label} must be an object")
            continue
        guide = guide_value
        identifier = guide.get("id")
        if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
            result.error(f"{label}.id must be a lowercase dotted identifier")
            identifier = f"<invalid-{index}>"
        elif identifier in guides_by_id:
            result.error(f"duplicate guide ID: {identifier}")
        else:
            guides_by_id[identifier] = guide

        parent_id = guide.get("parent_id")
        if parent_id is not None and (
            not isinstance(parent_id, str) or not ID_RE.fullmatch(parent_id)
        ):
            result.error(f"{label}.parent_id must be null or a lowercase dotted ID")

        title = guide.get("title")
        if not isinstance(title, str) or not title.strip():
            result.error(f"{label}.title must be a non-empty string")

        locale = guide.get("locale", default_locale)
        if not isinstance(locale, str) or locale not in supported_locales:
            result.error(f"{label}.locale must be one of supported_locales")
            locale = ""

        raw_path = guide.get("path")
        doc_path: Path | None = None
        if not isinstance(raw_path, str):
            result.error(f"{label}.path must be a repository-relative Markdown path")
        else:
            doc_path = _safe_repo_path(repo_root, raw_path)
            expected_prefix = f"{GUIDE_ROOT}/{locale}/" if locale else f"{GUIDE_ROOT}/"
            if doc_path is None or not raw_path.startswith(expected_prefix):
                result.error(
                    f"{label}.path must stay below {expected_prefix} using POSIX separators"
                )
                doc_path = None
            elif doc_path.suffix.lower() not in {".md", ".markdown"}:
                result.error(f"{label}.path must point to a Markdown file")
            elif not doc_path.is_file():
                result.error(f"{label}.path does not exist: {raw_path}")
            else:
                canonical_docs.add(doc_path)

        anchor = guide.get("anchor", "")
        if not isinstance(anchor, str) or (anchor and not ANCHOR_RE.fullmatch(anchor)):
            result.error(f"{label}.anchor must be empty or stable lowercase ASCII kebab-case")
            anchor = ""

        route = guide.get("route")
        if not isinstance(route, str) or not ROUTE_RE.fullmatch(route):
            result.error(
                f"{label}.route must match [a-z0-9.-]+(?::[a-z0-9.-]+)*"
            )
        elif locale:
            owner_key = (locale, route)
            if owner_key in route_owners:
                result.error(
                    f"duplicate route {route!r} for locale {locale}: "
                    f"{route_owners[owner_key]} and {identifier}"
                )
            route_owners[owner_key] = identifier

        source_paths = _as_string_list(
            guide.get("source_paths", []),
            label=f"{label}.source_paths",
            result=result,
        )
        for source_path in source_paths:
            fixed_prefix = source_path.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
            if _safe_repo_path(repo_root, fixed_prefix.rstrip("/")) is None:
                result.error(f"{label}.source_paths contains unsafe path: {source_path}")
            elif not _source_pattern_matches(repo_root, source_path):
                result.error(
                    f"{label}.source_paths does not match an existing path: {source_path}"
                )

        _as_string_list(
            guide.get("keywords", []),
            label=f"{label}.keywords",
            result=result,
        )

        risk = guide.get("risk", "low")
        if risk not in RISK_LEVELS:
            result.error(f"{label}.risk must be one of: {', '.join(sorted(RISK_LEVELS))}")

        capture_ids = _as_string_list(
            guide.get("qa_capture_ids", []),
            label=f"{label}.qa_capture_ids",
            result=result,
            allow_empty=True,
        )
        for capture_id in capture_ids:
            if not QA_ID_RE.fullmatch(capture_id):
                result.error(f"{label}.qa_capture_ids contains invalid ID: {capture_id}")
            elif qa_ids is not None and capture_id not in qa_ids:
                result.error(f"{label}.qa_capture_ids contains unknown scenario: {capture_id}")

        capability_ids = _as_string_list(
            guide.get("capability_ids", []),
            label=f"{label}.capability_ids",
            result=result,
            allow_empty=True,
        )
        for capability_id in capability_ids:
            if not ID_RE.fullmatch(capability_id):
                result.error(
                    f"{label}.capability_ids contains invalid ID: {capability_id}"
                )

        if doc_path is None or not doc_path.is_file():
            continue
        try:
            markdown = doc_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            result.error(f"cannot read {raw_path} as UTF-8: {exc}")
            continue
        if not markdown.strip():
            result.error(f"{raw_path}: guide is empty")
            continue
        headings, anchors = markdown_metadata(markdown)
        metadata_cache[doc_path] = (headings, anchors)
        normalized_headings = {_normalize_heading(value) for value in headings}
        for required_section in required_sections:
            if _normalize_heading(required_section) not in normalized_headings:
                result.error(f"{raw_path}: missing required section {required_section!r}")
        for pattern in PLACEHOLDER_PATTERNS:
            match = pattern.search(_strip_fenced_code(markdown))
            if match:
                result.error(f"{raw_path}: unresolved placeholder {match.group(0)!r}")
                break
        if anchor and anchor not in anchors:
            result.error(f"{raw_path}: guide anchor not found: {anchor}")
        quick_anchor = guide.get("quick_help_anchor", "")
        if not isinstance(quick_anchor, str) or (quick_anchor and not ANCHOR_RE.fullmatch(quick_anchor)):
            result.error(f"{label}.quick_help_anchor must be a stable lowercase ASCII anchor")
        elif quick_anchor and quick_anchor not in anchors:
            result.error(f"{raw_path}: quick help anchor not found: {quick_anchor}")
        if isinstance(raw_path, str):
            owner_key = (raw_path, anchor)
            if owner_key in anchor_owners:
                result.error(
                    f"duplicate guide path/anchor {raw_path}#{anchor}: "
                    f"{anchor_owners[owner_key]} and {identifier}"
                )
            anchor_owners[owner_key] = identifier
        _validate_links(
            repo_root=repo_root,
            doc_path=doc_path,
            markdown=markdown,
            result=result,
            metadata_cache=metadata_cache,
        )

    for identifier, guide in guides_by_id.items():
        parent_id = guide.get("parent_id")
        if parent_id is None:
            continue
        if parent_id not in guides_by_id:
            result.error(f"guide {identifier}: parent does not exist: {parent_id}")
            continue
        if guide.get("locale", default_locale) != guides_by_id[parent_id].get(
            "locale", default_locale
        ):
            result.error(f"guide {identifier}: parent must use the same locale")
    _validate_parent_graph(guides_by_id, result)

    user_root = repo_root / GUIDE_ROOT
    if user_root.exists():
        for markdown_path in sorted(user_root.rglob("*.md")):
            if markdown_path not in canonical_docs:
                result.error(
                    "orphan user-guide Markdown is not referenced by the manifest: "
                    f"{markdown_path.relative_to(repo_root).as_posix()}"
                )
    result.guides = [guide for guide in guides_value if isinstance(guide, dict)]


def _git_changed_paths(repo_root: Path, base: str, head: str) -> list[str]:
    command = [
        "git",
        "-c",
        f"safe.directory={repo_root.as_posix()}",
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        "--diff-filter=ACMRDTUXB",
        f"{base}...{head}",
        "--",
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"git diff failed for {base}...{head}: {detail}")

    fields = completed.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changed_paths: set[str] = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            raise ValueError(f"git diff returned an empty status for {base}...{head}")
        path_count = 2 if status[0] in {"R", "C"} else 1
        if index + path_count > len(fields):
            raise ValueError(f"git diff returned malformed name-status data for {base}...{head}")
        for raw_path in fields[index : index + path_count]:
            normalized = raw_path.replace("\\", "/")
            if normalized:
                changed_paths.add(normalized)
        index += path_count
    return sorted(changed_paths)


def source_pattern_matches_path(pattern: str, changed_path: str) -> bool:
    pattern = pattern.replace("\\", "/")
    changed_path = changed_path.replace("\\", "/")
    if any(character in pattern for character in "*?["):
        return fnmatch.fnmatchcase(changed_path, pattern) or PurePosixPath(changed_path).match(
            pattern
        )
    normalized = pattern.rstrip("/")
    return changed_path == normalized or changed_path.startswith(f"{normalized}/")


def _is_user_facing_source(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    return any(path == prefix or path.startswith(prefix) for prefix in USER_FACING_PREFIXES)


def _extract_exception_reason(value: str) -> str:
    if not value.strip():
        return ""
    key_present = re.search(
        r"(?im)^\s*(?:Guide exception reason|가이드 예외 사유)\s*:", value
    )
    if key_present:
        match = re.search(
            r"(?im)^\s*(?:Guide exception reason|가이드 예외 사유)\s*:\s*(\S.*)$",
            value,
        )
        return match.group(1).strip() if match else ""
    return value.strip()


def _valid_exception_reason(value: str) -> bool:
    reason = _extract_exception_reason(value)
    if len(reason) < 12:
        return False
    lowered = reason.lower()
    invalid_tokens = ("n/a", "none", "없음", "해당 없음", "작성", "todo", "tbd")
    return not any(token == lowered or token in lowered for token in invalid_tokens)


def _validate_diff_impact(
    *,
    repo_root: Path,
    result: ValidationResult,
    base: str,
    head: str,
    allow_guide_not_required: bool,
    exception_reason: str,
) -> None:
    try:
        changed_paths = _git_changed_paths(repo_root, base, head)
    except ValueError as exc:
        result.error(str(exc))
        return
    result.changed_paths = changed_paths

    affected: dict[str, dict[str, Any]] = {}
    mapped_source_changes: set[str] = set()
    for guide in result.guides:
        identifier = guide.get("id")
        if not isinstance(identifier, str):
            continue
        patterns = guide.get("source_paths", [])
        if not isinstance(patterns, list):
            continue
        for changed_path in changed_paths:
            if any(
                isinstance(pattern, str)
                and source_pattern_matches_path(pattern, changed_path)
                for pattern in patterns
            ):
                affected[identifier] = guide
                mapped_source_changes.add(changed_path)

    result.affected_ids = sorted(affected)
    missing_updates: list[str] = []
    for identifier, guide in sorted(affected.items()):
        guide_path = guide.get("path")
        if isinstance(guide_path, str) and guide_path not in changed_paths:
            missing_updates.append(f"{identifier} ({guide_path})")

    unmapped = sorted(
        path
        for path in changed_paths
        if _is_user_facing_source(path) and path not in mapped_source_changes
    )
    impact_errors: list[str] = []
    if missing_updates:
        impact_errors.append(
            "affected guides were not updated: " + ", ".join(missing_updates)
        )
    if unmapped:
        impact_errors.append(
            "user-facing source changes are not mapped by any guide: " + ", ".join(unmapped)
        )

    if not impact_errors:
        return
    if allow_guide_not_required:
        if not _valid_exception_reason(exception_reason):
            result.error(
                "guide-not-required was requested, but the PR must include a specific "
                "reason of at least 12 characters on a 'Guide exception reason:' line"
            )
            result.errors.extend(f"guide impact: {message}" for message in impact_errors)
            return
        reason = _extract_exception_reason(exception_reason)
        result.warn(f"guide impact exception accepted: {reason}")
        result.warnings.extend(f"guide impact exception: {message}" for message in impact_errors)
        return
    result.errors.extend(f"guide impact: {message}" for message in impact_errors)


def validate_repository(
    repo_root: Path,
    *,
    manifest_path: str = DEFAULT_MANIFEST,
    base: str | None = None,
    head: str = "HEAD",
    allow_guide_not_required: bool = False,
    exception_reason: str = "",
) -> ValidationResult:
    repo_root = repo_root.resolve()
    result = ValidationResult()
    resolved_manifest = _safe_repo_path(repo_root, manifest_path)
    if resolved_manifest is None:
        result.error(f"unsafe manifest path: {manifest_path}")
        return result
    try:
        manifest = load_manifest(resolved_manifest)
    except ValueError as exc:
        result.error(str(exc))
        return result
    result.manifest = manifest
    _validate_manifest_structure(repo_root, manifest, result)
    if any(guide.get("qa_capture_ids") for guide in result.guides):
        capture_config = repo_root / GUIDE_CAPTURE_CONFIG
        capture_manifest = repo_root / GUIDE_CAPTURE_MANIFEST
        for error in capture_manifest_errors(
            repo_root,
            capture_config,
            capture_manifest,
            assets_root=capture_manifest.parent,
        ):
            result.error(f"guide screenshot: {error}")
    # Diff impact is useful even when structural errors exist; showing both in one
    # run prevents a slow fix/rerun cycle in pull requests.
    if base:
        _validate_diff_impact(
            repo_root=repo_root,
            result=result,
            base=base,
            head=head,
            allow_guide_not_required=allow_guide_not_required,
            exception_reason=exception_reason,
        )
    return result


def _print_result(result: ValidationResult) -> None:
    if result.changed_paths:
        print(f"[guide-impact] changed paths: {len(result.changed_paths)}")
        if result.affected_ids:
            print("[guide-impact] affected guide IDs: " + ", ".join(result.affected_ids))
        else:
            print("[guide-impact] affected guide IDs: none")
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if result.ok:
        print(f"Guide validation passed ({len(result.guides)} guides).")
    else:
        print(f"Guide validation failed with {len(result.errors)} error(s).", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="repository root (defaults to the script's parent repository)",
    )
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--base", help="Git base revision for guide-impact validation")
    parser.add_argument("--head", default="HEAD", help="Git head revision (default: HEAD)")
    parser.add_argument(
        "--allow-guide-not-required",
        action="store_true",
        help="allow only diff-impact failures when a substantive exception reason is supplied",
    )
    parser.add_argument(
        "--exception-reason",
        default="",
        help="PR body or explicit reason for the guide-not-required exception",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.allow_guide_not_required and not args.base:
        print("ERROR: --allow-guide-not-required requires --base", file=sys.stderr)
        return 2
    result = validate_repository(
        Path(args.repo_root),
        manifest_path=args.manifest,
        base=args.base,
        head=args.head,
        allow_guide_not_required=args.allow_guide_not_required,
        exception_reason=args.exception_reason,
    )
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
