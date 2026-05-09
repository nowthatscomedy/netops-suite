from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from app.models.network_models import PublicIperfServer
from app.models.result_models import OperationResult
from app.utils.file_utils import AppPaths, load_json, save_json


class PublicIperfService:
    SOURCE_NAME = "R0GGER/public-iperf3-servers"
    SOURCE_URL = "https://github.com/R0GGER/public-iperf3-servers"
    EXPORT_JSON_URL = "https://export.iperf3serverlist.net/listed_iperf3_servers.json"
    REQUEST_TIMEOUT_SEC = 20
    AUTO_REFRESH_INTERVAL = timedelta(hours=12)

    def __init__(self, paths: AppPaths, logger: logging.Logger) -> None:
        self.paths = paths
        self.logger = logger

    def load_cached_servers(self) -> OperationResult:
        cache_data = load_json(self.paths.public_iperf_cache, {})
        if not isinstance(cache_data, dict):
            return OperationResult(False, "캐시된 공개 iperf 서버 목록이 없습니다.", payload=self._empty_payload())

        servers = self._deserialize_servers(cache_data.get("servers", []))
        fetched_at = str(cache_data.get("fetched_at", "") or "")
        if not servers:
            return OperationResult(False, "캐시된 공개 iperf 서버 목록이 없습니다.", payload=self._empty_payload())

        message = f"캐시된 공개 iperf 서버 {len(servers)}개를 불러왔습니다."
        return OperationResult(
            True,
            message,
            payload={
                "servers": servers,
                "fetched_at": fetched_at,
                "source": self.SOURCE_NAME,
                "source_url": self.SOURCE_URL,
                "from_cache": True,
                "stale": self.is_cache_stale(fetched_at),
            },
        )

    def fetch_public_servers(self, force_refresh: bool = False, progress_callback=None) -> OperationResult:
        cached = self.load_cached_servers()
        cached_payload = cached.payload if isinstance(cached.payload, dict) else self._empty_payload()
        fetched_at = str(cached_payload.get("fetched_at", "") or "")

        if not force_refresh and cached.success and not self.is_cache_stale(fetched_at):
            return OperationResult(
                True,
                cached.message,
                payload={**cached_payload, "used_cache_without_refresh": True},
            )

        self._emit_progress(progress_callback, "R0GGER 공개 iperf 서버 JSON 목록을 가져오는 중입니다...")
        try:
            export_items = self._download_json(self.EXPORT_JSON_URL)
            servers = self._parse_export_servers(export_items)
        except Exception as exc:
            self.logger.warning("Failed to refresh public iperf server list from JSON export: %s", exc)
            if cached.success:
                return OperationResult(
                    True,
                    "온라인 갱신에 실패해 캐시된 공개 iperf 서버 목록을 사용합니다.",
                    str(exc),
                    payload={**cached_payload, "refresh_failed": True, "from_cache": True},
                )
            return OperationResult(
                False,
                "공개 iperf 서버 목록을 가져오지 못했습니다.",
                str(exc),
                payload=self._empty_payload(),
            )

        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        save_json(
            self.paths.public_iperf_cache,
            {
                "fetched_at": fetched_at,
                "source": self.SOURCE_NAME,
                "source_url": self.SOURCE_URL,
                "servers": [server.to_dict() for server in servers],
            },
        )
        self.logger.info("Refreshed public iperf server cache with %s entries", len(servers))
        return OperationResult(
            True,
            f"공개 iperf 서버 {len(servers)}개를 업데이트했습니다.",
            payload={
                "servers": servers,
                "fetched_at": fetched_at,
                "source": self.SOURCE_NAME,
                "source_url": self.SOURCE_URL,
                "from_cache": False,
                "stale": False,
            },
        )

    def is_cache_stale(self, fetched_at: str) -> bool:
        if not fetched_at:
            return True
        try:
            normalized = fetched_at.replace("Z", "+00:00")
            fetched = datetime.fromisoformat(normalized)
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched > self.AUTO_REFRESH_INTERVAL

    def _download_json(self, url: str) -> list[dict]:
        request = urllib.request.Request(url, headers={"User-Agent": "NetOpsSuite/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.REQUEST_TIMEOUT_SEC) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read().decode(charset, errors="replace")
        except urllib.error.URLError as exc:
            raise ValueError("공개 iperf 서버 JSON 목록에 연결하지 못했습니다.") from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("공개 iperf 서버 JSON 형식을 해석하지 못했습니다.") from exc

        if not isinstance(data, list):
            raise ValueError("공개 iperf 서버 JSON 응답 형식이 예상과 다릅니다.")
        return [item for item in data if isinstance(item, dict)]

    def _parse_export_servers(self, items: list[dict]) -> list[PublicIperfServer]:
        servers: list[PublicIperfServer] = []
        seen_keys: set[str] = set()

        for item in items:
            host = str(item.get("IP/HOST", "") or "").strip()
            port_spec = str(item.get("PORT", "") or "").strip()
            if not host or not port_spec:
                continue

            key = f"{host}|{port_spec}"
            if key in seen_keys:
                continue
            seen_keys.add(key)

            country_code = str(item.get("COUNTRY", "") or "").strip()
            site_name = str(item.get("SITE", "") or "").strip() or host
            provider = str(item.get("PROVIDER", "") or "").strip()
            speed = str(item.get("GB/S", "") or "").strip()
            options = str(item.get("OPTIONS", "") or "").strip()
            region = str(item.get("CONTINENT", "") or "").strip()
            notes = f"제공자 {provider}" if provider else ""

            servers.append(
                PublicIperfServer(
                    name=site_name if not country_code else f"{site_name} ({country_code})",
                    host=host,
                    port_spec=port_spec,
                    default_port=self._default_port_from_spec(port_spec),
                    region=region,
                    country_code=country_code,
                    site=site_name,
                    speed=speed,
                    options=options,
                    source=self.SOURCE_NAME,
                    source_url=self.SOURCE_URL,
                    notes=notes,
                )
            )

        if not servers:
            raise ValueError("공개 iperf 서버 JSON에 사용할 수 있는 항목이 없습니다.")

        return servers

    def _default_port_from_spec(self, port_spec: str) -> int:
        first = port_spec.split(",", 1)[0].strip()
        if "-" in first:
            first = first.split("-", 1)[0].strip()
        try:
            return int(first)
        except ValueError:
            return 5201

    def _deserialize_servers(self, items: object) -> list[PublicIperfServer]:
        if not isinstance(items, list):
            return []
        servers: list[PublicIperfServer] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                servers.append(PublicIperfServer.from_dict(item))
            except (TypeError, ValueError):
                continue
        return servers

    def _emit_progress(self, progress_callback, message: str) -> None:
        if progress_callback is None or not message:
            return
        emitter = getattr(progress_callback, "emit", None)
        if callable(emitter):
            emitter(message)
        elif callable(progress_callback):
            progress_callback(message)

    def _empty_payload(self) -> dict[str, object]:
        return {
            "servers": [],
            "fetched_at": "",
            "source": self.SOURCE_NAME,
            "source_url": self.SOURCE_URL,
            "from_cache": False,
            "stale": True,
        }
