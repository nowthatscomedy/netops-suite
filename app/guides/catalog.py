from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


def extract_heading_section(markdown: str, anchor: str) -> str:
    """Extract a Markdown heading and its children, ignoring fenced code."""
    if not anchor:
        return markdown
    lines = markdown.splitlines(keepends=True)
    start: int | None = None
    level = 0
    fence = ""
    seen: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not heading:
            continue
        depth, title = len(heading.group(1)), heading.group(2)
        if start is not None and depth <= level:
            return "".join(lines[start:index]).strip()
        explicit = re.search(r"\s*\{#([^}]+)\}\s*$", title)
        plain = title[:explicit.start()] if explicit else title
        slug = re.sub(r"[^\w\s-]", "", plain.casefold())
        slug = re.sub(r"[\s-]+", "-", slug).strip("-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slug = f"{slug}-{count}" if count else slug
        targets = {slug, explicit.group(1).casefold() if explicit else ""}
        if anchor.casefold() in targets:
            start, level = index, depth
    return "".join(lines[start:]).strip() if start is not None else ""


@dataclass(frozen=True, slots=True)
class GuideEntry:
    """One user-guide document registered in the guide manifest."""

    id: str
    title: str
    locale: str
    path: str
    content_path: Path
    parent_id: str = ""
    anchor: str = ""
    quick_help_anchor: str = ""
    route: str = ""
    source_paths: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    risk: str = "low"
    qa_capture_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()


class GuideCatalog:
    """Read-only, failure-tolerant access to the bundled user guides."""

    def __init__(
        self,
        entries: Iterable[GuideEntry] = (),
        *,
        manifest_path: Path | None = None,
        project_root: Path | None = None,
        schema_version: int = 0,
        default_locale: str = "ko",
        supported_locales: Iterable[str] = ("ko",),
        required_sections: Iterable[str] = (),
        errors: Iterable[str] = (),
    ) -> None:
        self.entries = tuple(entries)
        self.manifest_path = manifest_path
        self.project_root = project_root or _source_project_root()
        self.schema_version = schema_version
        self.default_locale = default_locale
        self.supported_locales = tuple(supported_locales)
        self.required_sections = tuple(required_sections)
        self.errors = tuple(errors)
        self._by_id = {entry.id: entry for entry in self.entries}
        self._by_route = {
            entry.route: entry for entry in self.entries if entry.route
        }
        self._content_cache: dict[str, tuple[str | None, str | None]] = {}
        self._entries_by_path: dict[Path, list[GuideEntry]] = {}
        for entry in self.entries:
            try:
                resolved = entry.content_path.resolve(strict=False)
            except OSError:
                continue
            self._entries_by_path.setdefault(resolved, []).append(entry)

    @property
    def is_available(self) -> bool:
        return bool(self.entries)

    @classmethod
    def load(cls, project_root: str | Path | None = None) -> GuideCatalog:
        root = (
            Path(project_root).resolve(strict=False)
            if project_root is not None
            else _source_project_root()
        )
        diagnostics: list[str] = []
        candidates = _manifest_candidates(root)
        found_candidate = False
        for manifest_path in candidates:
            if not manifest_path.is_file():
                continue
            found_candidate = True
            try:
                catalog = cls._load_manifest(
                    manifest_path,
                    project_root=root,
                    prior_errors=diagnostics,
                )
                if any(entry.content_path.is_file() for entry in catalog.entries):
                    return catalog
                diagnostics.append(
                    f"가이드 매니페스트에 연결된 문서를 찾을 수 없습니다: {manifest_path}"
                )
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                diagnostics.append(
                    f"가이드 매니페스트를 읽을 수 없습니다: {manifest_path} ({exc})"
                )

        if not found_candidate:
            diagnostics.append(
                "가이드 매니페스트를 찾을 수 없습니다. 설치 파일을 복구하거나 "
                "소스 트리에서 가이드 번들을 다시 생성해 주세요."
            )
        return cls(project_root=root, errors=diagnostics)

    @classmethod
    def _load_manifest(
        cls,
        manifest_path: Path,
        *,
        project_root: Path,
        prior_errors: Iterable[str] = (),
    ) -> GuideCatalog:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("최상위 값은 JSON object여야 합니다")

        schema_version = payload.get("schema_version", 0)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ValueError("schema_version은 정수여야 합니다")

        raw_guides = payload.get("guides")
        if not isinstance(raw_guides, list):
            raise ValueError("guides는 배열이어야 합니다")

        default_locale = _clean_string(payload.get("default_locale")) or "ko"
        supported_locales = _string_tuple(payload.get("supported_locales"))
        if not supported_locales:
            supported_locales = (default_locale,)
        required_sections = _string_tuple(payload.get("required_sections"))
        content_root = _content_root_for_manifest(manifest_path, project_root)

        entries: list[GuideEntry] = []
        seen_ids: set[str] = set()
        entry_errors = list(prior_errors)
        for index, raw_entry in enumerate(raw_guides):
            if not isinstance(raw_entry, dict):
                entry_errors.append(f"guides[{index}] 항목이 object가 아니어서 건너뛰었습니다.")
                continue
            guide_id = _clean_string(raw_entry.get("id"))
            title = _clean_string(raw_entry.get("title"))
            raw_path = _clean_string(raw_entry.get("path"))
            if not guide_id or not title or not raw_path:
                entry_errors.append(
                    f"guides[{index}]에 필수 id/title/path가 없어 건너뛰었습니다."
                )
                continue
            if guide_id in seen_ids:
                entry_errors.append(f"중복 가이드 ID를 건너뛰었습니다: {guide_id}")
                continue
            try:
                content_path = _safe_content_path(content_root, raw_path)
            except ValueError as exc:
                entry_errors.append(f"{guide_id}: {exc}")
                continue

            seen_ids.add(guide_id)
            entries.append(
                GuideEntry(
                    id=guide_id,
                    parent_id=_clean_string(raw_entry.get("parent_id")),
                    title=title,
                    locale=_clean_string(raw_entry.get("locale")) or default_locale,
                    path=raw_path,
                    content_path=content_path,
                    anchor=_clean_string(raw_entry.get("anchor")),
                    quick_help_anchor=_clean_string(raw_entry.get("quick_help_anchor")),
                    route=_clean_string(raw_entry.get("route")) or guide_id,
                    source_paths=_string_tuple(raw_entry.get("source_paths")),
                    keywords=_string_tuple(raw_entry.get("keywords")),
                    risk=_normalized_risk(raw_entry.get("risk")),
                    qa_capture_ids=_string_tuple(raw_entry.get("qa_capture_ids")),
                    capability_ids=_string_tuple(raw_entry.get("capability_ids")),
                )
            )

        if not entries:
            raise ValueError("사용 가능한 가이드 항목이 없습니다")
        return cls(
            entries,
            manifest_path=manifest_path,
            project_root=project_root,
            schema_version=schema_version,
            default_locale=default_locale,
            supported_locales=supported_locales,
            required_sections=required_sections,
            errors=entry_errors,
        )

    def get(self, guide_id: str) -> GuideEntry | None:
        return self._by_id.get(str(guide_id or "").strip())

    def resolve(self, guide_id_or_route: str) -> GuideEntry | None:
        """Resolve an exact ID/route, then walk up a dotted context ID."""

        candidate = str(guide_id_or_route or "").strip()
        while candidate:
            entry = self._by_id.get(candidate) or self._by_route.get(candidate)
            if entry is not None:
                return entry
            candidate = candidate.rpartition(".")[0]
        return None

    def welcome_entry(self) -> GuideEntry | None:
        return self.get("getting-started") or self.default_entry()

    def default_entry(self) -> GuideEntry | None:
        if not self.entries:
            return None
        for entry in self.entries:
            if not entry.parent_id and entry.locale == self.default_locale:
                return entry
        return self.entries[0]

    def read_markdown(self, entry: GuideEntry) -> tuple[str | None, str | None]:
        cached = self._content_cache.get(entry.id)
        if cached is not None:
            return cached
        try:
            if not entry.content_path.is_file():
                raise FileNotFoundError(entry.content_path)
            content = entry.content_path.read_text(encoding="utf-8")
            result = (content, None)
        except (OSError, UnicodeError) as exc:
            result = (
                None,
                f"가이드 문서를 읽을 수 없습니다: {entry.content_path} ({exc})",
            )
        self._content_cache[entry.id] = result
        return result

    def read_topic(self, entry: GuideEntry) -> tuple[str | None, str | None]:
        """Read the owned document, or only the topic in a shared document."""
        markdown, error = self.read_markdown(entry)
        if markdown is None:
            return None, error
        owners = self._entries_by_path.get(entry.content_path.resolve(strict=False), ())
        if len(owners) <= 1:
            return markdown, None
        section = extract_heading_section(markdown, entry.anchor)
        if not section:
            return None, "요청한 도움말 주제를 찾을 수 없습니다."
        return section, None

    def read_quick_help(self, entry: GuideEntry) -> tuple[str | None, str | None]:
        if not entry.quick_help_anchor:
            return self.read_topic(entry)
        markdown, error = self.read_markdown(entry)
        if markdown is None:
            return None, error
        section = extract_heading_section(markdown, entry.quick_help_anchor)
        if not section:
            return None, "이 화면의 짧은 도움말을 찾을 수 없습니다."
        return section, None

    def search_score(self, entry: GuideEntry, query: str) -> int | None:
        normalized = str(query or "").strip().casefold()
        if not normalized:
            return 0
        terms = tuple(part.casefold() for part in query.split() if part.strip())
        title = entry.title.casefold()
        aliases = tuple(value.casefold() for value in entry.keywords)
        if normalized == title:
            return 0
        if normalized in aliases or normalized in {entry.id, entry.route}:
            return 1
        if all(term in title for term in terms):
            return 2
        metadata = "\n".join((title, *aliases, entry.id, entry.route)).casefold()
        if all(term in metadata for term in terms):
            return 3
        topic, _error = self.read_topic(entry)
        quick, _error = self.read_quick_help(entry)
        searchable = "\n".join((metadata, topic or "", quick or "")).casefold()
        return 4 if all(term in searchable for term in terms) else None

    def search(self, query: str) -> tuple[GuideEntry, ...]:
        scored = [(score, index, entry) for index, entry in enumerate(self.entries)
                  if (score := self.search_score(entry, query)) is not None]
        return tuple(entry for _score, _index, entry in sorted(scored, key=lambda item: item[:2]))

    def matches(self, entry: GuideEntry, query: str) -> bool:
        return self.search_score(entry, query) is not None

    def entry_for_link(
        self,
        current_entry: GuideEntry,
        link_path: str,
        fragment: str = "",
    ) -> GuideEntry | None:
        decoded = unquote(str(link_path or ""))
        if not decoded:
            candidates = self._entries_by_path.get(
                current_entry.content_path.resolve(strict=False), []
            )
            return _entry_with_anchor(candidates, fragment) or current_entry

        relative = Path(decoded)
        if relative.is_absolute():
            path_candidates = [relative]
        else:
            content_root = (
                _content_root_for_manifest(self.manifest_path, self.project_root)
                if self.manifest_path is not None
                else self.project_root
            )
            path_candidates = [
                current_entry.content_path.parent / relative,
                content_root / relative,
                self.project_root / relative,
            ]
        for candidate in path_candidates:
            try:
                entries = self._entries_by_path.get(candidate.resolve(strict=False), [])
            except OSError:
                continue
            match = _entry_with_anchor(entries, fragment)
            if match is not None:
                return match
        return None


def _manifest_candidates(project_root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if bool(getattr(sys, "frozen", False)):
        bundle_root = getattr(sys, "_MEIPASS", "")
        if bundle_root:
            candidates.append(
                Path(bundle_root) / "app" / "resources" / "guides" / "guide_manifest.json"
            )
        executable_root = Path(sys.executable).resolve().parent
        candidates.extend(
            (
                executable_root
                / "_internal"
                / "app"
                / "resources"
                / "guides"
                / "guide_manifest.json",
                executable_root
                / "app"
                / "resources"
                / "guides"
                / "guide_manifest.json",
            )
        )
    else:
        candidates.extend(
            (
                project_root
                / "app"
                / "resources"
                / "guides"
                / "guide_manifest.json",
                project_root / "docs" / "guide_manifest.json",
            )
        )
    return tuple(dict.fromkeys(candidates))


def _source_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _content_root_for_manifest(manifest_path: Path, project_root: Path) -> Path:
    try:
        manifest_path.resolve(strict=False).relative_to(
            (project_root / "docs").resolve(strict=False)
        )
    except ValueError:
        return manifest_path.parent.resolve(strict=False)
    return project_root.resolve(strict=False)


def _safe_content_path(content_root: Path, raw_path: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute():
        raise ValueError("문서 path는 상대 경로여야 합니다")
    candidate = (content_root / relative).resolve(strict=False)
    try:
        candidate.relative_to(content_root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("문서 path가 가이드 루트를 벗어납니다") from exc
    return candidate


def _clean_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _normalized_risk(value: Any) -> str:
    normalized = _clean_string(value).casefold()
    return normalized if normalized in {"low", "medium", "high"} else "low"


def _entry_with_anchor(
    entries: Iterable[GuideEntry], fragment: str
) -> GuideEntry | None:
    choices = tuple(entries)
    if fragment:
        for entry in choices:
            if entry.anchor == fragment:
                return entry
    return choices[0] if choices else None
