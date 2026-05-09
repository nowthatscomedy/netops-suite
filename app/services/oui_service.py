from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from io import StringIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.models.network_models import OuiRecord
from app.models.result_models import OperationResult
from app.utils.file_utils import AppPaths, save_json


class OuiService:
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
        self.ensure_loaded()
        if not self.records:
            return "OUI 캐시 없음"
        updated_text = self.updated_at or "알 수 없음"
        return f"로컬 캐시 {len(self.records):,}건 | 기준 시각 {updated_text}"

    def refresh_cache(self, progress_callback=None) -> OperationResult:
        fetched_records: list[OuiRecord] = []
        errors: list[str] = []

        for registry, url in self.IEEE_SOURCES:
            try:
                if progress_callback is not None:
                    progress_callback.emit(f"[OUI] {registry} 목록 다운로드 중: {url}")
                fetched_records.extend(self._fetch_registry(registry, url))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{registry}: {exc}")
                self.logger.warning("OUI registry fetch failed for %s: %s", registry, exc)

        if not fetched_records:
            return OperationResult(
                False,
                "OUI 캐시를 갱신하지 못했습니다.",
                "\n".join(errors) if errors else "사용 가능한 레지스트리를 하나도 가져오지 못했습니다.",
            )

        deduped: dict[tuple[str, int], OuiRecord] = {}
        for record in fetched_records:
            deduped[(record.prefix, record.prefix_bits)] = record

        self.records = sorted(
            deduped.values(),
            key=lambda item: (item.prefix_bits, item.prefix, item.organization.lower()),
            reverse=True,
        )
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._loaded = True
        save_json(
            self.paths.oui_cache,
            {
                "updated_at": self.updated_at,
                "records": [asdict(record) for record in self.records],
            },
        )
        self.logger.info("Saved OUI cache with %s records.", len(self.records))

        details = self.cache_summary()
        if errors:
            details += "\n\n부분 실패:\n" + "\n".join(errors)
        return OperationResult(True, "OUI 캐시를 갱신했습니다.", details, {"count": len(self.records)})

    def _load_cache(self) -> None:
        self._loaded = True
        if not self.paths.oui_cache.exists():
            self.records = []
            self.updated_at = ""
            return

        try:
            payload = json.loads(self.paths.oui_cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to read OUI cache: %s", exc)
            self.records = []
            self.updated_at = ""
            return

        raw_records = payload.get("records", []) if isinstance(payload, dict) else []
        self.updated_at = str(payload.get("updated_at", "") or "") if isinstance(payload, dict) else ""
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

    def _fetch_registry(self, registry: str, url: str) -> list[OuiRecord]:
        request = Request(url, headers=dict(self.REQUEST_HEADERS))
        try:
            with urlopen(request, timeout=25) as response:
                content = response.read().decode("utf-8-sig", errors="replace")
        except URLError as exc:
            raise RuntimeError(f"다운로드 실패: {exc}") from exc

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
        return records

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
