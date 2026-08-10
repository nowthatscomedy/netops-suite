from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote


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

    def matches(self, entry: GuideEntry, query: str) -> bool:
        terms = tuple(part.casefold() for part in query.split() if part.strip())
        if not terms:
            return True
        content, _error = self.read_markdown(entry)
        searchable = "\n".join(
            (
                entry.id,
                entry.route,
                entry.title,
                " ".join(entry.capability_ids),
                " ".join(entry.keywords),
                content or "",
            )
        ).casefold()
        return all(term in searchable for term in terms)

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
