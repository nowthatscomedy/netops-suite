from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from io import StringIO
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.models.network_models import OuiRecord
from app.models.result_models import OperationResult
from app.utils.file_utils import AppPaths, save_json


class OuiService:
    CACHE_SCHEMA_VERSION = 2
    SOURCE_NAME = "IEEE Registration Authority"
    SOURCE_URL = "https://standards-oui.ieee.org/"
    STALE_AFTER_DAYS = 30
    MAC_FRAGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}(?:[:\-\.\s][0-9a-f]{2}){2,7})(?![0-9a-f])"),
        re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{4}(?:[:\-\.\s][0-9a-f]{4}){1,2})(?![0-9a-f])"),
        re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{6,12}(?![0-9a-f])"),
    )
    LABEL_ONLY_RE = re.compile(
        r"(?i)^(?:"
        r"mac(?:\s*address)?|mac\s*주소|"
        r"physical\s*address|wireless\s*address|"
        r"bssid|oui|address|addr|"
        r"물리적\s*주소|무선\s*주소|주소"
        r")\s*[:=\-]?\s*$"
    )
    IEEE_SOURCES: tuple[tuple[str, str], ...] = (
        ("MA-L", "https://standards-oui.ieee.org/oui/oui.csv"),
        ("MA-M", "https://standards-oui.ieee.org/oui28/mam.csv"),
        ("MA-S", "https://standards-oui.ieee.org/oui36/oui36.csv"),
        ("CID", "https://standards-oui.ieee.org/cid/cid.csv"),
    )
    REQUEST_HEADERS = {
        "User-Agent": "NetOps-Suite/1.0 (+https://github.com/nowthatscomedy/netops-suite)",
        "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8",
    }

    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger
        self.records: list[OuiRecord] = []
        self.updated_at = ""
        self.dataset_version = ""
        self.source_updated_at = ""
        self.source_metadata: dict[str, dict[str, Any]] = {}
        self.last_checked_at = ""
        self._loaded = False

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_cache()

    def lookup(self, mac_address: str) -> OuiRecord | None:
        self.ensure_loaded()
        normalized = self.normalize_mac(mac_address)
        if not normalized:
            return None

        best_match: OuiRecord | None = None
        for record in self.records:
            prefix_length = record.prefix_bits // 4
            if normalized.startswith(record.prefix[:prefix_length]):
                if best_match is None or record.prefix_bits > best_match.prefix_bits:
                    best_match = record
        return best_match

    def lookup_vendor(self, mac_address: str) -> str:
        match = self.lookup(mac_address)
        return match.organization if match else ""

    def cache_summary(self) -> str:
        status = self.cache_status()
        if not status["available"]:
            return "OUI 캐시 없음"
        updated_text = str(status["updated_at"] or "알 수 없음")
        version_text = str(status["version_label"] or "버전 정보 없음")
        freshness = "업데이트 확인 필요" if status["stale"] else "로컬 데이터 사용 가능"
        return (
            f"IEEE OUI {status['record_count']:,}건 | {version_text} | "
            f"갱신 {updated_text} | {freshness}"
        )

    def cache_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Return local cache metadata without making any network request."""
        self.ensure_loaded()
        current_time = now or datetime.now().astimezone()
        updated_datetime = self._parse_datetime(self.updated_at)
        age_days: int | None = None
        if updated_datetime is not None:
            if updated_datetime.tzinfo is None and current_time.tzinfo is not None:
                updated_datetime = updated_datetime.replace(tzinfo=current_time.tzinfo)
            if updated_datetime.tzinfo is not None and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=updated_datetime.tzinfo)
            age = max(current_time - updated_datetime, timedelta())
            age_days = age.days

        available = bool(self.records)
        stale = (
            not available
            or age_days is None
            or age_days >= self.STALE_AFTER_DAYS
        )
        return {
            "available": available,
            "cache_path": str(self.paths.oui_cache),
            "record_count": len(self.records),
            "updated_at": self.updated_at,
            "age_days": age_days,
            "stale": stale,
            "stale_after_days": self.STALE_AFTER_DAYS,
            "dataset_version": self.dataset_version,
            "version_label": self._version_label(self.dataset_version),
            "source_name": self.SOURCE_NAME,
            "source_url": self.SOURCE_URL,
            "source_updated_at": self.source_updated_at,
            "sources": {
                registry: dict(metadata)
                for registry, metadata in self.source_metadata.items()
            },
            "last_checked_at": self.last_checked_at,
        }

    def check_for_updates(self, progress_callback=None) -> OperationResult:
        """Compare the local cache with all authoritative IEEE registries.

        This method intentionally downloads and parses the remote registries only
        when called. It never writes the cache or replaces in-memory records.
        """
        local_status = self.cache_status()
        records, source_metadata, errors = self._fetch_all_registries(
            progress_callback,
            action_text="최신 여부 확인",
        )
        checked_at = self._now_text()
        self.last_checked_at = checked_at
        if errors:
            details = (
                "현재 OUI 캐시는 변경하지 않았습니다.\n\n"
                + "\n".join(errors)
            )
            self.logger.warning(
                "OUI update check failed; local cache was preserved: %s",
                " | ".join(errors),
            )
            return OperationResult(
                False,
                "IEEE OUI 데이터의 최신 여부를 확인하지 못했습니다.",
                details,
                {
                    "update_available": None,
                    "is_latest": None,
                    "local": local_status,
                    "checked_at": checked_at,
                    "errors": errors,
                },
            )

        deduped_records = self._deduplicate_records(records)
        remote_version = self._records_version(deduped_records)
        update_available = (
            not local_status["available"]
            or not self.dataset_version
            or self.dataset_version != remote_version
        )
        remote_status = self._remote_status(
            deduped_records,
            source_metadata,
            remote_version,
            checked_at,
        )
        local_version = str(local_status["version_label"] or "버전 정보 없음")
        details = "\n".join(
            (
                f"원본: {self.SOURCE_NAME}",
                f"로컬: {local_version} / {local_status['record_count']:,}건",
                (
                    f"온라인: {remote_status['version_label']} / "
                    f"{remote_status['record_count']:,}건"
                ),
                f"확인 시각: {checked_at}",
            )
        )
        if update_available:
            message = "최신 IEEE OUI 데이터가 있습니다."
        else:
            message = "OUI 데이터가 최신 상태입니다."
        self.logger.info(
            "Checked IEEE OUI data: local=%s remote=%s update_available=%s",
            self.dataset_version or "unknown",
            remote_version,
            update_available,
        )
        return OperationResult(
            True,
            message,
            details,
            {
                "update_available": update_available,
                "is_latest": not update_available,
                "local": local_status,
                "remote": remote_status,
                "checked_at": checked_at,
            },
        )

    def refresh_cache(self, progress_callback=None) -> OperationResult:
        fetched_records, source_metadata, errors = self._fetch_all_registries(
            progress_callback,
            action_text="업데이트",
        )
        if errors:
            self.logger.warning(
                "OUI refresh aborted; local cache was preserved: %s",
                " | ".join(errors),
            )
            return OperationResult(
                False,
                "OUI 캐시를 갱신하지 못했습니다.",
                (
                    "IEEE 레지스트리 4개를 모두 확인하지 못해 기존 캐시를 "
                    "그대로 유지했습니다.\n\n"
                    + "\n".join(errors)
                ),
            )

        replacement_records = self._deduplicate_records(fetched_records)
        if not replacement_records:
            return OperationResult(
                False,
                "OUI 캐시를 갱신하지 못했습니다.",
                "IEEE 레지스트리에서 사용할 수 있는 OUI 항목을 찾지 못해 기존 캐시를 유지했습니다.",
            )

        updated_at = self._now_text()
        dataset_version = self._records_version(replacement_records)
        source_updated_at = self._latest_source_timestamp(source_metadata)
        payload = {
            "schema_version": self.CACHE_SCHEMA_VERSION,
            "source_name": self.SOURCE_NAME,
            "source_url": self.SOURCE_URL,
            "updated_at": updated_at,
            "dataset_version": dataset_version,
            "source_updated_at": source_updated_at,
            "sources": source_metadata,
            "records": [asdict(record) for record in replacement_records],
        }
        try:
            save_json(self.paths.oui_cache, payload)
        except OSError as exc:
            self.logger.exception("Failed to save refreshed OUI cache.")
            return OperationResult(
                False,
                "OUI 캐시 파일을 저장하지 못했습니다.",
                f"기존 캐시는 그대로 유지했습니다.\n\n{exc}",
            )

        self.records = replacement_records
        self.updated_at = updated_at
        self.dataset_version = dataset_version
        self.source_updated_at = source_updated_at
        self.source_metadata = {
            registry: dict(metadata)
            for registry, metadata in source_metadata.items()
        }
        self._loaded = True
        self.logger.info(
            "Saved IEEE OUI cache with %s records (version=%s).",
            len(self.records),
            self.dataset_version,
        )

        status = self.cache_status()
        details = "\n".join(
            (
                f"원본: {self.SOURCE_NAME}",
                f"데이터: {status['record_count']:,}건",
                f"버전: {status['version_label']}",
                f"갱신 시각: {status['updated_at']}",
            )
        )
        return OperationResult(
            True,
            "OUI 데이터를 최신 상태로 업데이트했습니다.",
            details,
            {
                "count": len(self.records),
                "dataset_version": self.dataset_version,
                "version_label": status["version_label"],
                "updated_at": self.updated_at,
                "source_updated_at": self.source_updated_at,
            },
        )

    def _load_cache(self) -> None:
        self._loaded = True
        if not self.paths.oui_cache.exists():
            self.records = []
            self.updated_at = ""
            self.dataset_version = ""
            self.source_updated_at = ""
            self.source_metadata = {}
            return

        try:
            payload = json.loads(self.paths.oui_cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to read OUI cache: %s", exc)
            self.records = []
            self.updated_at = ""
            self.dataset_version = ""
            self.source_updated_at = ""
            self.source_metadata = {}
            return

        raw_records = payload.get("records", []) if isinstance(payload, dict) else []
        self.updated_at = str(payload.get("updated_at", "") or "") if isinstance(payload, dict) else ""
        self.dataset_version = (
            str(payload.get("dataset_version", "") or "")
            if isinstance(payload, dict)
            else ""
        )
        self.source_updated_at = (
            str(payload.get("source_updated_at", "") or "")
            if isinstance(payload, dict)
            else ""
        )
        raw_sources = payload.get("sources", {}) if isinstance(payload, dict) else {}
        self.source_metadata = {
            str(registry): dict(metadata)
            for registry, metadata in raw_sources.items()
            if isinstance(registry, str) and isinstance(metadata, dict)
        } if isinstance(raw_sources, dict) else {}
        self.records = [
            OuiRecord(
                prefix=str(item.get("prefix", "") or ""),
                prefix_bits=int(item.get("prefix_bits", 0) or 0),
                organization=str(item.get("organization", "") or ""),
                registry=str(item.get("registry", "") or ""),
            )
            for item in raw_records
            if isinstance(item, dict)
        ]

    def _fetch_all_registries(
        self,
        progress_callback,
        *,
        action_text: str,
    ) -> tuple[list[OuiRecord], dict[str, dict[str, Any]], list[str]]:
        records: list[OuiRecord] = []
        source_metadata: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for registry, url in self.IEEE_SOURCES:
            try:
                self._emit_progress(
                    progress_callback,
                    f"[OUI] {registry} {action_text} 중: {url}",
                )
                registry_records, metadata = self._fetch_registry_payload(
                    registry,
                    url,
                )
                if not registry_records:
                    raise RuntimeError("사용 가능한 항목이 없습니다.")
                records.extend(registry_records)
                source_metadata[registry] = metadata
            except Exception as exc:  # noqa: BLE001
                error = f"{registry}: {exc}"
                errors.append(error)
                self.logger.warning(
                    "OUI registry fetch failed for %s: %s",
                    registry,
                    exc,
                )
        return records, source_metadata, errors

    def _fetch_registry(self, registry: str, url: str) -> list[OuiRecord]:
        records, _metadata = self._fetch_registry_payload(registry, url)
        return records

    def _fetch_registry_payload(
        self,
        registry: str,
        url: str,
    ) -> tuple[list[OuiRecord], dict[str, Any]]:
        request = Request(url, headers=dict(self.REQUEST_HEADERS))
        try:
            with urlopen(request, timeout=25) as response:
                raw_content = response.read()
                headers = getattr(response, "headers", None)
        except URLError as exc:
            raise RuntimeError(f"다운로드 실패: {exc}") from exc

        content = raw_content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(StringIO(content))
        records: list[OuiRecord] = []
        for row in reader:
            assignment = self._clean_assignment(
                str(row.get("Assignment") or row.get("MA-L") or row.get("MA-M") or row.get("MA-S") or row.get("CID") or "")
            )
            organization = str(row.get("Organization Name") or row.get("Organization") or "").strip()
            if not assignment or not organization:
                continue
            records.append(
                OuiRecord(
                    prefix=assignment,
                    prefix_bits=len(assignment) * 4,
                    organization=organization,
                    registry=registry,
                )
            )
        metadata = {
            "registry": registry,
            "url": url,
            "record_count": len(records),
            "sha256": hashlib.sha256(raw_content).hexdigest(),
            "etag": self._header_value(headers, "ETag"),
            "last_modified": self._header_value(headers, "Last-Modified"),
            "fetched_at": self._now_text(),
        }
        return records, metadata

    @staticmethod
    def _header_value(headers: Any, name: str) -> str:
        getter = getattr(headers, "get", None)
        if not callable(getter):
            return ""
        return str(getter(name, "") or "").strip()

    @staticmethod
    def _emit_progress(progress_callback: Any, message: str) -> None:
        if progress_callback is None or not message:
            return
        emitter = getattr(progress_callback, "emit", None)
        if callable(emitter):
            emitter(message)
            return
        if callable(progress_callback):
            progress_callback(message)

    @staticmethod
    def _deduplicate_records(records: list[OuiRecord]) -> list[OuiRecord]:
        deduped: dict[tuple[str, int], OuiRecord] = {}
        for record in records:
            deduped[(record.prefix, record.prefix_bits)] = record
        return sorted(
            deduped.values(),
            key=lambda item: (
                item.prefix_bits,
                item.prefix,
                item.organization.casefold(),
                item.registry,
            ),
            reverse=True,
        )

    @staticmethod
    def _records_version(records: list[OuiRecord]) -> str:
        digest = hashlib.sha256()
        for record in records:
            digest.update(
                (
                    f"{record.registry}\0{record.prefix}\0{record.prefix_bits}\0"
                    f"{record.organization}\n"
                ).encode("utf-8")
            )
        return digest.hexdigest()

    @classmethod
    def _version_label(cls, dataset_version: str) -> str:
        if not dataset_version:
            return ""
        return f"SHA-256 {dataset_version[:12]}"

    @classmethod
    def _remote_status(
        cls,
        records: list[OuiRecord],
        source_metadata: dict[str, dict[str, Any]],
        dataset_version: str,
        checked_at: str,
    ) -> dict[str, Any]:
        return {
            "source_name": cls.SOURCE_NAME,
            "source_url": cls.SOURCE_URL,
            "record_count": len(records),
            "dataset_version": dataset_version,
            "version_label": cls._version_label(dataset_version),
            "source_updated_at": cls._latest_source_timestamp(source_metadata),
            "sources": {
                registry: dict(metadata)
                for registry, metadata in source_metadata.items()
            },
            "checked_at": checked_at,
        }

    @staticmethod
    def _latest_source_timestamp(
        source_metadata: dict[str, dict[str, Any]],
    ) -> str:
        candidates: list[tuple[datetime, str]] = []
        for metadata in source_metadata.values():
            raw_value = str(metadata.get("last_modified", "") or "").strip()
            if not raw_value:
                continue
            try:
                parsed = parsedate_to_datetime(raw_value)
            except (TypeError, ValueError):
                continue
            candidates.append((parsed, raw_value))
        if not candidates:
            return ""
        return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None

    @staticmethod
    def _now_text() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @classmethod
    def extract_mac_fragment(cls, mac_address: str) -> str:
        text = str(mac_address or "").strip()
        if not text:
            return ""
        for pattern in cls.MAC_FRAGMENT_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0).strip()
        return ""

    @classmethod
    def split_label_and_mac(cls, value: str) -> tuple[str, str]:
        text = str(value or "").strip()
        if not text:
            return "", ""

        if "," in text:
            name, candidate = [part.strip() for part in text.split(",", 1)]
            fragment = cls.extract_mac_fragment(candidate) or candidate
            return name or fragment, fragment

        fragment = cls.extract_mac_fragment(text)
        if not fragment:
            return text, text

        label = text.replace(fragment, " ", 1).strip(" -:|/[]()")
        if not label or cls.LABEL_ONLY_RE.match(label):
            label = fragment
        return label, fragment

    @classmethod
    def normalize_mac(cls, mac_address: str) -> str:
        fragment = cls.extract_mac_fragment(mac_address)
        text = "".join(ch for ch in fragment.upper() if ch in "0123456789ABCDEF")
        if len(text) < 6:
            return ""
        return text

    @staticmethod
    def _clean_assignment(value: str) -> str:
        cleaned = "".join(ch for ch in value.upper() if ch in "0123456789ABCDEF")
        return cleaned
