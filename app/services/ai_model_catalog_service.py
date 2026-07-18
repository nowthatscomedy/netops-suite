from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from app.models.ai_models import (
    AI_INPUT_MODALITIES,
    AI_REASONING_EFFORTS,
    KNOWN_AI_PROVIDERS,
    AiModelCatalog,
    AiModelDescriptor,
    AiProviderConfig,
)
from app.services.ai_agent_service import (
    FALLBACK_MODEL_OPTIONS,
    resolve_provider_program,
    safe_env_for_cli,
)
from app.utils.file_utils import load_json, save_json
from app.utils.process_utils import no_window_creationflags


MODEL_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
CLI_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:/() -]{0,199}\Z")


class AiModelCatalogError(RuntimeError):
    pass


class ModelCatalogAdapter(Protocol):
    provider_key: str
    supports_live_discovery: bool

    def cli_identity(
        self,
        service: "AiModelCatalogService",
        config: AiProviderConfig,
        deadline: float,
        cancel_event: threading.Event | None,
    ) -> tuple[str, str]: ...

    def discover(
        self,
        service: "AiModelCatalogService",
        config: AiProviderConfig,
        cli_path: str,
        cli_version: str,
        cancel_event: threading.Event | None,
        deadline: float,
    ) -> AiModelCatalog: ...


class CodexModelCatalogAdapter:
    provider_key = "codex"
    supports_live_discovery = True

    def cli_identity(
        self,
        service: "AiModelCatalogService",
        config: AiProviderConfig,
        deadline: float,
        cancel_event: threading.Event | None,
    ) -> tuple[str, str]:
        program = resolve_provider_program(config)
        return program, service._read_cli_version(program, deadline, cancel_event)

    def discover(
        self,
        service: "AiModelCatalogService",
        config: AiProviderConfig,
        cli_path: str,
        cli_version: str,
        cancel_event: threading.Event | None,
        deadline: float,
    ) -> AiModelCatalog:
        return service._discover_codex(cli_path, cli_version, cancel_event, deadline)


class FallbackModelCatalogAdapter:
    supports_live_discovery = False

    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key

    def cli_identity(
        self,
        service: "AiModelCatalogService",
        config: AiProviderConfig,
        deadline: float,
        cancel_event: threading.Event | None,
    ) -> tuple[str, str]:
        return "", ""

    def discover(
        self,
        service: "AiModelCatalogService",
        config: AiProviderConfig,
        cli_path: str,
        cli_version: str,
        cancel_event: threading.Event | None,
        deadline: float,
    ) -> AiModelCatalog:
        return service.fallback_catalog(self.provider_key, config.model)


class AiModelCatalogService:
    CACHE_VERSION = 1
    CACHE_TTL = timedelta(hours=6)
    _CACHE_UPDATE_LOCK = threading.RLock()
    DISCOVERY_TIMEOUT_SECONDS = 20.0
    VERSION_TIMEOUT_SECONDS = 5.0
    PAGE_LIMIT = 100
    MAX_PAGES = 5
    MAX_MODELS = 500

    def __init__(
        self,
        cache_path: Path,
        adapters: dict[str, ModelCatalogAdapter] | None = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.adapters: dict[str, ModelCatalogAdapter] = adapters or {
            "codex": CodexModelCatalogAdapter(),
            "claude": FallbackModelCatalogAdapter("claude"),
            "gemini": FallbackModelCatalogAdapter("gemini"),
        }

    def load_catalog(
        self, provider_key: str, current_model: str = ""
    ) -> AiModelCatalog:
        payload = load_json(self.cache_path, {})
        if isinstance(payload, dict) and payload.get("version") == self.CACHE_VERSION:
            providers = payload.get("providers", {})
            raw_catalog = (
                providers.get(provider_key) if isinstance(providers, dict) else None
            )
            if isinstance(raw_catalog, dict):
                try:
                    catalog = AiModelCatalog.from_dict(raw_catalog)
                    catalog = self._validated_catalog(
                        catalog,
                        expected_provider=provider_key,
                        allowed_catalog_sources={"live", "cache"},
                        allow_custom=False,
                        allow_hidden=False,
                    )
                    catalog.source = "cache"
                    return self._with_current_model(catalog, current_model)
                except (TypeError, ValueError):
                    pass
        return self.fallback_catalog(provider_key, current_model)

    def fallback_catalog(
        self, provider_key: str, current_model: str = ""
    ) -> AiModelCatalog:
        models: list[AiModelDescriptor] = []
        for label, model in FALLBACK_MODEL_OPTIONS.get(provider_key, ()):
            if not model:
                continue
            models.append(
                AiModelDescriptor(
                    id=model,
                    model=model,
                    display_name=label,
                    source="fallback",
                )
            )
        catalog = AiModelCatalog(
            provider_key=provider_key, models=models, source="fallback"
        )
        return self._with_current_model(catalog, current_model)

    def needs_refresh(
        self,
        catalog: AiModelCatalog,
        cli_path: str = "",
        cli_version: str = "",
        now: datetime | None = None,
    ) -> bool:
        if catalog.provider_key != "codex":
            return False
        if catalog.source not in {"live", "cache"} or not catalog.models:
            return True
        if cli_path and self._normalized_path(
            catalog.cli_path
        ) != self._normalized_path(cli_path):
            return True
        if cli_version and catalog.cli_version != cli_version:
            return True
        fetched_at = self._parse_timestamp(catalog.fetched_at)
        if fetched_at is None:
            return True
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        age = reference.astimezone(timezone.utc) - fetched_at
        return age < timedelta(0) or age >= self.CACHE_TTL

    def cli_identity(
        self,
        config: AiProviderConfig,
        deadline: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str]:
        effective_deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + self.DISCOVERY_TIMEOUT_SECONDS
        )
        return self._adapter_for(config.key).cli_identity(
            self, config, effective_deadline, cancel_event
        )

    def discover(
        self,
        config: AiProviderConfig,
        cancel_event: threading.Event | None = None,
    ) -> AiModelCatalog:
        return self.refresh(config, None, True, cancel_event)

    def refresh(
        self,
        config: AiProviderConfig,
        current_catalog: AiModelCatalog | None = None,
        force: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> AiModelCatalog:
        deadline = time.monotonic() + self.DISCOVERY_TIMEOUT_SECONDS
        self._raise_if_cancelled(cancel_event)
        adapter = self._adapter_for(config.key)
        program, cli_version = self.cli_identity(config, deadline, cancel_event)
        if not adapter.supports_live_discovery:
            return adapter.discover(
                self, config, program, cli_version, cancel_event, deadline
            )
        if (
            current_catalog is not None
            and not force
            and not self.needs_refresh(
                current_catalog,
                program,
                cli_version,
            )
        ):
            return self._with_current_model(current_catalog, config.model)
        catalog = adapter.discover(
            self, config, program, cli_version, cancel_event, deadline
        )
        catalog = self._validated_catalog(
            catalog,
            expected_provider=config.key,
            allowed_catalog_sources={"live"},
            allow_custom=False,
            allow_hidden=False,
        )
        self.save_catalog(catalog)
        return self._with_current_model(catalog, config.model)

    def _adapter_for(self, provider_key: str) -> ModelCatalogAdapter:
        try:
            return self.adapters[provider_key]
        except KeyError as exc:
            raise ValueError(
                f"지원하지 않는 AI provider입니다: {provider_key}"
            ) from exc

    def save_catalog(self, catalog: AiModelCatalog) -> None:
        if catalog.source not in {"live", "cache"}:
            return
        cacheable_models: list[AiModelDescriptor] = []
        for model in catalog.models:
            if not isinstance(model, AiModelDescriptor):
                raise ValueError(
                    "AI model catalog contains an invalid model descriptor"
                )
            if not model.hidden and model.source != "custom":
                cacheable_models.append(model)
        if not cacheable_models:
            raise ValueError("빈 모델 목록은 캐시에 저장할 수 없습니다.")
        cacheable = AiModelCatalog(
            provider_key=catalog.provider_key,
            models=cacheable_models,
            fetched_at=catalog.fetched_at,
            cli_path=catalog.cli_path,
            cli_version=catalog.cli_version,
            source=catalog.source,
        )
        cacheable = self._validated_catalog(
            cacheable,
            expected_provider=catalog.provider_key,
            allowed_catalog_sources={"live", "cache"},
            allow_custom=False,
            allow_hidden=False,
        )
        with self._CACHE_UPDATE_LOCK:
            payload = load_json(self.cache_path, {})
            if (
                not isinstance(payload, dict)
                or payload.get("version") != self.CACHE_VERSION
            ):
                payload = {"version": self.CACHE_VERSION, "providers": {}}
            providers = payload.get("providers")
            if not isinstance(providers, dict):
                providers = {}
            stored = AiModelCatalog(
                provider_key=cacheable.provider_key,
                models=list(cacheable.models),
                fetched_at=cacheable.fetched_at,
                cli_path=cacheable.cli_path,
                cli_version=cacheable.cli_version,
                source="live",
            )
            providers[cacheable.provider_key] = stored.to_dict()
            payload["version"] = self.CACHE_VERSION
            payload["providers"] = providers
            save_json(self.cache_path, payload)

    def _discover_codex(
        self,
        program: str,
        cli_version: str,
        cancel_event: threading.Event | None,
        deadline: float | None = None,
    ) -> AiModelCatalog:
        process: subprocess.Popen[str] | None = None
        output_queue: queue.Queue[str | None] = queue.Queue()
        effective_deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + self.DISCOVERY_TIMEOUT_SECONDS
        )
        try:
            self._raise_if_cancelled(cancel_event)
            self._raise_if_deadline_expired(effective_deadline)
            process = subprocess.Popen(
                [program, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=safe_env_for_cli(),
                creationflags=no_window_creationflags(),
                bufsize=1,
            )
            stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(process, output_queue),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            request_id = 1
            self._send(
                process,
                {
                    "id": request_id,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "netops-suite", "version": "1"},
                    },
                },
            )
            self._wait_for_result(
                process, output_queue, request_id, effective_deadline, cancel_event
            )
            self._send(process, {"method": "initialized"})

            models: list[AiModelDescriptor] = []
            seen_ids: set[str] = set()
            seen_models: set[str] = set()
            seen_cursors: set[str] = set()
            cursor: str | None = None
            for page_index in range(self.MAX_PAGES):
                request_id += 1
                params: dict[str, Any] = {
                    "limit": self.PAGE_LIMIT,
                    "includeHidden": False,
                }
                if cursor:
                    params["cursor"] = cursor
                self._send(
                    process,
                    {"id": request_id, "method": "model/list", "params": params},
                )
                result = self._wait_for_result(
                    process,
                    output_queue,
                    request_id,
                    effective_deadline,
                    cancel_event,
                )
                raw_models = result.get("data")
                if not isinstance(raw_models, list):
                    raise AiModelCatalogError(
                        "Codex 모델 목록 응답 형식이 올바르지 않습니다."
                    )
                for raw_model in raw_models:
                    descriptor = self._descriptor_from_app_server(raw_model)
                    if descriptor.hidden:
                        continue
                    if descriptor.id in seen_ids:
                        raise AiModelCatalogError(
                            f"Codex 모델 목록에 중복 항목(ID)이 있습니다: {descriptor.id}"
                        )
                    if descriptor.model in seen_models:
                        raise AiModelCatalogError(
                            f"Codex 모델 목록에 중복 항목이 있습니다: {descriptor.model}"
                        )
                    seen_ids.add(descriptor.id)
                    seen_models.add(descriptor.model)
                    models.append(descriptor)
                    if len(models) > self.MAX_MODELS:
                        raise AiModelCatalogError(
                            "Codex 모델 목록이 허용 범위를 초과했습니다."
                        )

                next_cursor = result.get("nextCursor")
                if next_cursor is None or next_cursor == "":
                    cursor = None
                    break
                if not isinstance(next_cursor, str):
                    raise AiModelCatalogError(
                        "Codex 모델 목록 cursor 형식이 올바르지 않습니다."
                    )
                if next_cursor in seen_cursors:
                    raise AiModelCatalogError(
                        "Codex 모델 목록 cursor가 반복되었습니다."
                    )
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                if page_index == self.MAX_PAGES - 1:
                    raise AiModelCatalogError(
                        "Codex 모델 목록 페이지 한도를 초과했습니다."
                    )

            if not models:
                raise AiModelCatalogError(
                    "현재 계정에서 사용할 수 있는 Codex 모델을 찾지 못했습니다."
                )
            if sum(1 for model in models if model.is_default) > 1:
                raise AiModelCatalogError("Codex 기본 모델 정보가 중복되었습니다.")
            return AiModelCatalog(
                provider_key="codex",
                models=models,
                fetched_at=self._utc_now_text(),
                cli_path=program,
                cli_version=cli_version,
                source="live",
            )
        except FileNotFoundError as exc:
            raise AiModelCatalogError("Codex CLI 실행 파일을 찾지 못했습니다.") from exc
        except OSError as exc:
            raise AiModelCatalogError(
                f"Codex 모델 목록 조회를 시작하지 못했습니다: {exc}"
            ) from exc
        finally:
            self._stop_process(process)

    @staticmethod
    def _read_stdout(
        process: subprocess.Popen[str], output_queue: queue.Queue[str | None]
    ) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    output_queue.put(line.rstrip("\r\n"))
        finally:
            output_queue.put(None)

    @staticmethod
    def _read_stderr(process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for _line in process.stderr:
            pass

    @staticmethod
    def _send(process: subprocess.Popen[str], payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise AiModelCatalogError("Codex app-server 입력 스트림을 열지 못했습니다.")
        process.stdin.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        process.stdin.flush()

    def _wait_for_result(
        self,
        process: subprocess.Popen[str],
        output_queue: queue.Queue[str | None],
        request_id: int,
        deadline: float,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise AiModelCatalogError("모델 목록 조회가 취소되었습니다.")
            try:
                remaining = max(0.0, deadline - time.monotonic())
                line = output_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    raise AiModelCatalogError(self._process_failure_text(process))
                continue
            if line is None:
                if process.poll() is not None:
                    raise AiModelCatalogError(self._process_failure_text(process))
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AiModelCatalogError(
                    "Codex app-server가 잘못된 JSON을 반환했습니다."
                ) from exc
            if not isinstance(payload, dict):
                raise AiModelCatalogError(
                    "Codex app-server 응답 형식이 올바르지 않습니다."
                )
            if payload.get("id") != request_id:
                continue
            error = payload.get("error")
            if error is not None:
                raise AiModelCatalogError("Codex 모델 목록 요청이 실패했습니다.")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise AiModelCatalogError(
                    "Codex app-server 결과 형식이 올바르지 않습니다."
                )
            return result
        raise AiModelCatalogError("Codex 모델 목록 조회 시간이 초과되었습니다.")

    @staticmethod
    def _process_failure_text(process: subprocess.Popen[str]) -> str:
        return f"Codex app-server가 종료되었습니다. (종료 코드 {process.returncode})"

    @staticmethod
    def _stop_process(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

    @staticmethod
    def _descriptor_from_app_server(raw_model: Any) -> AiModelDescriptor:
        if not isinstance(raw_model, dict):
            raise AiModelCatalogError("Codex 모델 항목 형식이 올바르지 않습니다.")

        model_id = raw_model.get("id", "")
        model_value = raw_model.get("model", "")
        display_name = raw_model.get("displayName", "")
        default_reasoning = raw_model.get("defaultReasoningEffort", "")
        is_default = raw_model.get("isDefault", False)
        hidden = raw_model.get("hidden", False)
        if not all(
            isinstance(item, str)
            for item in (model_id, model_value, display_name, default_reasoning)
        ):
            raise AiModelCatalogError(
                "Codex 모델 문자열 필드 형식이 올바르지 않습니다."
            )
        if not isinstance(is_default, bool) or not isinstance(hidden, bool):
            raise AiModelCatalogError(
                "Codex 모델 boolean 필드 형식이 올바르지 않습니다."
            )
        if model_id != model_id.strip() or model_value != model_value.strip():
            raise AiModelCatalogError("Codex 모델 ID 형식이 올바르지 않습니다.")
        default_reasoning = default_reasoning.strip()
        if not MODEL_VALUE_RE.fullmatch(model_id) or not MODEL_VALUE_RE.fullmatch(
            model_value
        ):
            raise AiModelCatalogError("Codex 모델 ID 형식이 올바르지 않습니다.")

        raw_reasoning = raw_model.get("supportedReasoningEfforts", [])
        if not isinstance(raw_reasoning, list):
            raise AiModelCatalogError("Codex 모델 추론 정보 형식이 올바르지 않습니다.")
        reasoning: list[str] = []
        for item in raw_reasoning:
            if not isinstance(item, dict):
                raise AiModelCatalogError(
                    "Codex 모델 추론 항목 형식이 올바르지 않습니다."
                )
            effort = item.get("reasoningEffort", "")
            if not isinstance(effort, str):
                raise AiModelCatalogError(
                    "Codex 모델 추론 단계 형식이 올바르지 않습니다."
                )
            effort = effort.strip()
            if effort not in AI_REASONING_EFFORTS:
                raise AiModelCatalogError(
                    f"지원하지 않는 Codex 추론 단계입니다: {effort or '(없음)'}"
                )
            if effort not in reasoning:
                reasoning.append(effort)
        raw_modalities = raw_model.get("inputModalities", ["text", "image"])
        if (
            not isinstance(raw_modalities, list)
            or not raw_modalities
            or any(not isinstance(item, str) for item in raw_modalities)
            or any(item not in AI_INPUT_MODALITIES for item in raw_modalities)
        ):
            raise AiModelCatalogError("Codex 모델 입력 형식이 올바르지 않습니다.")
        raw_speed = raw_model.get("additionalSpeedTiers", [])
        if not isinstance(raw_speed, list) or any(
            not isinstance(item, str) for item in raw_speed
        ):
            raise AiModelCatalogError("Codex 모델 속도 정보 형식이 올바르지 않습니다.")
        speed_tiers = list(dict.fromkeys(item for item in raw_speed if item == "fast"))

        availability = raw_model.get("availabilityNux")
        if availability is not None and not isinstance(availability, dict):
            raise AiModelCatalogError(
                "Codex 모델 가용성 정보 형식이 올바르지 않습니다."
            )
        availability_message = (
            availability.get("message", "") if isinstance(availability, dict) else ""
        )
        if not isinstance(availability_message, str):
            raise AiModelCatalogError(
                "Codex 모델 가용성 메시지 형식이 올바르지 않습니다."
            )

        upgrade = raw_model.get("upgrade", "")
        if upgrade is None:
            upgrade = ""
        if not isinstance(upgrade, str):
            raise AiModelCatalogError(
                "Codex 모델 업그레이드 정보 형식이 올바르지 않습니다."
            )
        upgrade_info = raw_model.get("upgradeInfo")
        if upgrade_info is not None and not isinstance(upgrade_info, dict):
            raise AiModelCatalogError(
                "Codex 모델 업그레이드 정보 형식이 올바르지 않습니다."
            )
        if not upgrade and isinstance(upgrade_info, dict):
            upgrade = upgrade_info.get("model", "")
            if not isinstance(upgrade, str):
                raise AiModelCatalogError(
                    "Codex 모델 업그레이드 대상 형식이 올바르지 않습니다."
                )

        if not default_reasoning or default_reasoning not in reasoning:
            raise AiModelCatalogError(
                "Codex 기본 추론 단계가 지원 목록과 일치하지 않습니다."
            )
        payload = {
            "id": model_id,
            "model": model_value,
            "display_name": display_name[:160],
            "supported_reasoning_efforts": reasoning,
            "default_reasoning_effort": default_reasoning,
            "input_modalities": raw_modalities,
            "speed_tiers": speed_tiers,
            "is_default": is_default,
            "hidden": hidden,
            "upgrade": upgrade[:128],
            "availability_message": availability_message[:500],
            "source": "live",
        }
        try:
            return AiModelDescriptor.from_dict(payload)
        except ValueError as exc:
            raise AiModelCatalogError(str(exc)) from exc

    def _read_cli_version(
        self,
        program: str,
        deadline: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        effective_deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + self.DISCOVERY_TIMEOUT_SECONDS
        )
        probe_deadline = min(
            effective_deadline, time.monotonic() + self.VERSION_TIMEOUT_SECONDS
        )
        process: subprocess.Popen[str] | None = None
        try:
            self._raise_if_cancelled(cancel_event)
            self._raise_if_deadline_expired(effective_deadline)
            process = subprocess.Popen(
                [program, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=safe_env_for_cli(),
                creationflags=no_window_creationflags(),
            )
            while True:
                self._raise_if_cancelled(cancel_event)
                self._raise_if_deadline_expired(effective_deadline)
                remaining = probe_deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                try:
                    stdout, _stderr = process.communicate(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
                if process.returncode != 0:
                    return ""
                first_line = next(
                    (
                        line.strip()
                        for line in (stdout or "").splitlines()
                        if line.strip()
                    ),
                    "",
                )
                return first_line if CLI_VERSION_RE.fullmatch(first_line) else ""
        except OSError:
            return ""
        finally:
            self._stop_process(process)

    @classmethod
    def _validated_catalog(
        cls,
        catalog: AiModelCatalog,
        *,
        expected_provider: str,
        allowed_catalog_sources: set[str],
        allow_custom: bool,
        allow_hidden: bool,
    ) -> AiModelCatalog:
        if not isinstance(catalog, AiModelCatalog):
            raise ValueError("AI model catalog has an invalid type")
        string_fields = (
            catalog.provider_key,
            catalog.fetched_at,
            catalog.cli_path,
            catalog.cli_version,
            catalog.source,
        )
        if not all(isinstance(item, str) for item in string_fields):
            raise ValueError("AI model catalog contains an invalid string field")
        if (
            catalog.provider_key not in KNOWN_AI_PROVIDERS
            or catalog.provider_key != expected_provider
        ):
            raise ValueError("AI model catalog provider does not match its cache key")
        if catalog.source not in allowed_catalog_sources:
            raise ValueError("AI model catalog contains an invalid source")
        if not catalog.models:
            raise ValueError("빈 모델 목록은 캐시에 저장할 수 없습니다.")
        if cls._parse_timestamp(catalog.fetched_at) is None:
            raise ValueError("AI model catalog contains an invalid fetched timestamp")
        if len(catalog.models) > cls.MAX_MODELS:
            raise ValueError("AI model catalog exceeds the model limit")

        models: list[AiModelDescriptor] = []
        seen_ids: set[str] = set()
        seen_models: set[str] = set()
        default_count = 0
        for model in catalog.models:
            if not isinstance(model, AiModelDescriptor):
                raise ValueError(
                    "AI model catalog contains an invalid model descriptor"
                )
            normalized = AiModelDescriptor.from_dict(model.to_dict())
            if not MODEL_VALUE_RE.fullmatch(
                normalized.id
            ) or not MODEL_VALUE_RE.fullmatch(normalized.model):
                raise ValueError("AI model catalog contains an invalid model ID")
            if len(normalized.display_name) > 160 or len(normalized.upgrade) > 128:
                raise ValueError("AI model catalog contains oversized model metadata")
            if len(normalized.availability_message) > 500:
                raise ValueError(
                    "AI model catalog contains oversized availability metadata"
                )
            if normalized.source == "custom" and not allow_custom:
                raise ValueError("AI model catalog cache cannot contain custom models")
            if not allow_custom and normalized.source not in {"live", "cache"}:
                raise ValueError("AI model catalog cache contains a non-live model")
            if normalized.hidden and not allow_hidden:
                raise ValueError("AI model catalog cache cannot contain hidden models")
            if normalized.id in seen_ids:
                raise ValueError(
                    f"AI 모델 카탈로그에 중복 ID가 있습니다: {normalized.id}"
                )
            if normalized.model in seen_models:
                raise ValueError(
                    f"AI 모델 카탈로그에 중복 모델 값이 있습니다: {normalized.model}"
                )
            seen_ids.add(normalized.id)
            seen_models.add(normalized.model)
            default_count += int(normalized.is_default)
            models.append(normalized)
        if default_count > 1:
            raise ValueError("AI model catalog contains multiple default models")

        return AiModelCatalog(
            provider_key=catalog.provider_key,
            models=models,
            fetched_at=catalog.fetched_at,
            cli_path=catalog.cli_path,
            cli_version=catalog.cli_version,
            source=catalog.source,
        )

    @staticmethod
    def _with_current_model(
        catalog: AiModelCatalog, current_model: str
    ) -> AiModelCatalog:
        result = AiModelCatalog(
            provider_key=catalog.provider_key,
            models=list(catalog.models),
            fetched_at=catalog.fetched_at,
            cli_path=catalog.cli_path,
            cli_version=catalog.cli_version,
            source=catalog.source,
        )
        selected = str(current_model or "").strip()
        if selected and selected not in {model.model for model in result.models}:
            result.models.append(
                AiModelDescriptor(
                    id=selected,
                    model=selected,
                    display_name=selected,
                    source="custom",
                )
            )
        return result

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AiModelCatalogError("모델 목록 조회가 취소되었습니다.")

    @staticmethod
    def _raise_if_deadline_expired(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise AiModelCatalogError("Codex 모델 목록 조회 시간이 초과되었습니다.")

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _utc_now_text() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalized_path(value: str) -> str:
        text = str(value or "").strip()
        return os.path.normcase(os.path.normpath(text)) if text else ""
