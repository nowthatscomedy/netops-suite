from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.ai_models import AiModelCatalog, AiModelDescriptor, AiProviderConfig
from app.services.ai_model_catalog_service import (
    AiModelCatalogError,
    AiModelCatalogService,
)


class _FakeStdin:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, value: str) -> int:
        self.lines.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(
        self, stdout_lines: list[str], stderr_lines: list[str] | None = None
    ) -> None:
        self.stdin = _FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = iter(stderr_lines or [])
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class _StubbornFakeProcess(_FakeProcess):
    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        if not self.killed:
            raise subprocess.TimeoutExpired("codex", timeout)
        return self.returncode or -9


class _BlockingVersionProcess:
    def __init__(self, on_communicate) -> None:
        self.stdin = None
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.on_communicate = on_communicate
        self.communicate_calls = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        self.on_communicate()
        raise subprocess.TimeoutExpired("codex --version", timeout)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _response(request_id: int, result: dict[str, Any]) -> str:
    return json.dumps({"id": request_id, "result": result}) + "\n"


def _error_response(request_id: int, message: str) -> str:
    return (
        json.dumps({"id": request_id, "error": {"code": -32603, "message": message}})
        + "\n"
    )


def _raw_model(
    model: str,
    *,
    display_name: str | None = None,
    is_default: bool = False,
    hidden: bool = False,
    fast: bool = False,
    modalities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": f"id-{model}",
        "model": model,
        "displayName": display_name or model,
        "description": "Provider description must not be shown verbatim in the basic UI.",
        "hidden": hidden,
        "isDefault": is_default,
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low"},
            {"reasoningEffort": "high"},
            {"reasoningEffort": "xhigh"},
        ],
        "defaultReasoningEffort": "high",
        "inputModalities": modalities or ["text", "image"],
        "additionalSpeedTiers": ["fast"] if fast else [],
        "serviceTiers": [{"id": "priority"}],
        "upgrade": "next-model",
        "availabilityNux": {"message": "available"},
    }


def test_codex_app_server_protocol_parses_paginated_model_catalog(
    tmp_path, monkeypatch
):
    process = _FakeProcess(
        [
            _response(1, {}),
            _response(
                2,
                {
                    "data": [
                        _raw_model(
                            "gpt-default",
                            display_name="GPT 기본",
                            is_default=True,
                            fast=True,
                        ),
                        _raw_model("hidden-model", hidden=True),
                    ],
                    "nextCursor": "page-2",
                },
            ),
            _response(
                3,
                {
                    "data": [_raw_model("gpt-text", modalities=["text"])],
                    "nextCursor": None,
                },
            ),
        ]
    )
    popen_calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(argv, **kwargs):
        popen_calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen", fake_popen
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service,
        "cli_identity",
        lambda _config, *_args, **_kwargs: ("C:/Codex/codex.exe", "codex-cli 9.9"),
    )

    catalog = service.discover(AiProviderConfig(key="codex"))

    assert popen_calls[0][0] == [
        "C:/Codex/codex.exe",
        "app-server",
        "--listen",
        "stdio://",
    ]
    assert popen_calls[0][1].get("shell") is None
    requests = [json.loads(line) for line in process.stdin.lines]
    assert [request["method"] for request in requests] == [
        "initialize",
        "initialized",
        "model/list",
        "model/list",
    ]
    assert requests[2]["params"] == {"limit": 100, "includeHidden": False}
    assert requests[3]["params"]["cursor"] == "page-2"
    assert [model.model for model in catalog.models] == ["gpt-default", "gpt-text"]
    assert catalog.default_model == "gpt-default"
    assert catalog.models[0].supported_reasoning_efforts == ["low", "high", "xhigh"]
    assert catalog.models[0].input_modalities == ["text", "image"]
    assert catalog.models[0].speed_tiers == ["fast"]
    assert "priority" not in catalog.models[0].speed_tiers
    assert process.stdin.closed is True
    assert process.terminated is True
    assert service.load_catalog("codex").source == "cache"


def test_codex_catalog_accepts_gpt56_max_and_ultra_reasoning_and_caches_them(
    tmp_path, monkeypatch
):
    sol = _raw_model(
        "gpt-5.6-sol", display_name="GPT-5.6-Sol", is_default=True, fast=True
    )
    sol["supportedReasoningEfforts"] = [
        {"reasoningEffort": value}
        for value in ("low", "medium", "high", "xhigh", "max", "ultra")
    ]
    sol["defaultReasoningEffort"] = "low"
    luna = _raw_model("gpt-5.6-luna", display_name="GPT-5.6-Luna", fast=True)
    luna["supportedReasoningEfforts"] = [
        {"reasoningEffort": value}
        for value in ("low", "medium", "high", "xhigh", "max")
    ]
    luna["defaultReasoningEffort"] = "medium"
    process = _FakeProcess(
        [
            _response(1, {}),
            _response(2, {"data": [sol, luna], "nextCursor": None}),
        ]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service,
        "cli_identity",
        lambda _config, *_args, **_kwargs: ("C:/Codex/codex.cmd", "codex-cli 0.144.1"),
    )

    catalog = service.discover(AiProviderConfig(key="codex"))

    assert catalog.default_model == "gpt-5.6-sol"
    assert catalog.models[0].supported_reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert catalog.models[1].supported_reasoning_efforts == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    cached = service.load_catalog("codex")
    assert cached.models[0].supported_reasoning_efforts[-2:] == ["max", "ultra"]
    assert cached.models[1].supported_reasoning_efforts[-1] == "max"


@pytest.mark.parametrize(
    "stdout_lines, expected_message",
    [
        (["not-json\n"], "잘못된 JSON"),
        (
            [_response(1, {}), _response(2, {"data": [], "nextCursor": None})],
            "찾지 못했습니다",
        ),
        (
            [
                _response(1, {}),
                _response(
                    2,
                    {
                        "data": [_raw_model("same"), _raw_model("same")],
                        "nextCursor": None,
                    },
                ),
            ],
            "중복 항목",
        ),
        (
            [
                _response(1, {}),
                _response(2, {"data": [_raw_model("one")], "nextCursor": "same"}),
                _response(3, {"data": [_raw_model("two")], "nextCursor": "same"}),
            ],
            "cursor가 반복",
        ),
    ],
)
def test_codex_discovery_rejects_invalid_responses_and_stops_process(
    tmp_path,
    monkeypatch,
    stdout_lines,
    expected_message,
):
    process = _FakeProcess(stdout_lines)
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    with pytest.raises(AiModelCatalogError, match=expected_message):
        service.discover(AiProviderConfig(key="codex"))

    assert process.stdin.closed is True
    assert process.terminated is True
    assert not (tmp_path / "catalog.json").exists()


@pytest.mark.parametrize(
    ("case", "expected_message"),
    [
        ("rpc_error", "요청이 실패"),
        ("cursor_type", "cursor 형식"),
        ("duplicate_id", "중복.*ID"),
        ("multiple_defaults", "기본 모델 정보가 중복"),
    ],
)
def test_codex_discovery_rejects_rpc_and_catalog_identity_errors(
    tmp_path,
    monkeypatch,
    case,
    expected_message,
):
    stdout_lines = [_response(1, {})]
    if case == "rpc_error":
        stdout_lines.append(_error_response(2, "account model lookup failed"))
    elif case == "cursor_type":
        stdout_lines.append(
            _response(2, {"data": [_raw_model("one")], "nextCursor": 123})
        )
    elif case == "duplicate_id":
        first = _raw_model("one")
        second = _raw_model("two")
        second["id"] = first["id"]
        stdout_lines.append(_response(2, {"data": [first, second], "nextCursor": None}))
    else:
        stdout_lines.append(
            _response(
                2,
                {
                    "data": [
                        _raw_model("one", is_default=True),
                        _raw_model("two", is_default=True),
                    ],
                    "nextCursor": None,
                },
            )
        )

    process = _FakeProcess(stdout_lines)
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    with pytest.raises(AiModelCatalogError, match=expected_message):
        service.discover(AiProviderConfig(key="codex"))

    assert process.stdin.closed is True
    assert process.terminated is True
    assert not service.cache_path.exists()


def test_codex_discovery_enforces_page_and_model_count_limits(tmp_path, monkeypatch):
    page_process = _FakeProcess(
        [_response(1, {})]
        + [
            _response(
                request_id,
                {
                    "data": [_raw_model(f"page-model-{page_index}")],
                    "nextCursor": f"page-{page_index + 1}",
                },
            )
            for page_index, request_id in enumerate(range(2, 7))
        ]
    )
    processes = iter([page_process])
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: next(processes),
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    with pytest.raises(AiModelCatalogError, match="페이지 한도"):
        service.discover(AiProviderConfig(key="codex"))

    requests = [json.loads(line) for line in page_process.stdin.lines]
    assert sum(request["method"] == "model/list" for request in requests) == 5
    assert page_process.terminated is True

    model_process = _FakeProcess(
        [
            _response(1, {}),
            _response(
                2,
                {
                    "data": [_raw_model(f"model-{index}") for index in range(501)],
                    "nextCursor": None,
                },
            ),
        ]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: model_process,
    )

    with pytest.raises(AiModelCatalogError, match="허용 범위"):
        service.discover(AiProviderConfig(key="codex"))

    assert model_process.terminated is True
    assert not service.cache_path.exists()


def test_codex_discovery_preserves_server_reasoning_order(tmp_path, monkeypatch):
    raw_model = _raw_model("ordered-reasoning")
    raw_model["supportedReasoningEfforts"] = [
        {"reasoningEffort": "xhigh"},
        {"reasoningEffort": "none"},
        {"reasoningEffort": "medium"},
    ]
    raw_model["defaultReasoningEffort"] = "medium"
    process = _FakeProcess(
        [_response(1, {}), _response(2, {"data": [raw_model], "nextCursor": None})]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    catalog = service.discover(AiProviderConfig(key="codex"))

    assert catalog.models[0].supported_reasoning_efforts == ["xhigh", "none", "medium"]


def test_codex_discovery_timeout_stops_process(tmp_path, monkeypatch):
    process = _FakeProcess([])
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    service.DISCOVERY_TIMEOUT_SECONDS = 0.03
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    with pytest.raises(AiModelCatalogError, match="시간이 초과"):
        service.discover(AiProviderConfig(key="codex"))

    assert process.terminated is True


def test_codex_discovery_kills_process_when_terminate_does_not_finish(
    tmp_path, monkeypatch
):
    process = _StubbornFakeProcess(
        [
            _response(1, {}),
            _response(2, {"data": [_raw_model("model")], "nextCursor": None}),
        ]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    catalog = service.discover(AiProviderConfig(key="codex"))

    assert [model.model for model in catalog.models] == ["model"]
    assert process.terminated is True
    assert process.killed is True


@pytest.mark.parametrize(
    "mutation, expected_message",
    [
        (
            lambda model: model.update(
                supportedReasoningEfforts=[{"reasoningEffort": "mystery"}]
            ),
            "지원하지 않는",
        ),
        (lambda model: model.update(model="invalid model"), "모델 ID 형식"),
        (lambda model: model.update(inputModalities=[]), "입력 형식"),
        (
            lambda model: model.update(defaultReasoningEffort="minimal"),
            "기본 추론 단계",
        ),
    ],
)
def test_codex_discovery_rejects_inconsistent_model_metadata(
    tmp_path,
    monkeypatch,
    mutation,
    expected_message,
):
    raw_model = _raw_model("valid-model")
    mutation(raw_model)
    process = _FakeProcess(
        [_response(1, {}), _response(2, {"data": [raw_model], "nextCursor": None})]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    with pytest.raises(AiModelCatalogError, match=expected_message):
        service.discover(AiProviderConfig(key="codex"))

    assert process.terminated is True


def test_codex_discovery_cancellation_and_start_failure_leave_no_cache(
    tmp_path, monkeypatch
):
    process = _FakeProcess([_response(1, {})])
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "catalog.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )
    cancel_event = Event()
    cancel_event.set()

    with pytest.raises(AiModelCatalogError, match="취소"):
        service.discover(AiProviderConfig(key="codex"), cancel_event)
    assert process.stdin.lines == []
    assert process.terminated is False
    assert not service.cache_path.exists()

    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cannot start")),
    )
    with pytest.raises(AiModelCatalogError, match="시작하지 못했습니다"):
        service.discover(AiProviderConfig(key="codex"))
    assert not service.cache_path.exists()


def test_version_probe_honors_cancellation_and_full_deadline(tmp_path, monkeypatch):
    service = AiModelCatalogService(tmp_path / "catalog.json")
    cancel_event = Event()
    process = _BlockingVersionProcess(cancel_event.set)
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    started = time.monotonic()
    with pytest.raises(AiModelCatalogError, match="취소"):
        service._read_cli_version("codex", started + 2, cancel_event)

    assert time.monotonic() - started < 0.5
    assert process.communicate_calls == 1
    assert process.terminated is True

    popen_called = False

    def fail_if_started(*_args, **_kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError(
            "expired overall deadline must stop before starting a version process"
        )

    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen", fail_if_started
    )
    with pytest.raises(AiModelCatalogError, match="시간이 초과"):
        service._read_cli_version("codex", time.monotonic() - 0.01, Event())
    assert popen_called is False


def test_refresh_uses_one_deadline_for_version_identity_and_discovery(tmp_path):
    class DeadlineAdapter:
        provider_key = "codex"
        supports_live_discovery = True

        def __init__(self) -> None:
            self.identity_deadline: float | None = None
            self.discovery_deadline: float | None = None
            self.cancel_event: Event | None = None

        def cli_identity(self, service, config, deadline, cancel_event):
            self.identity_deadline = deadline
            self.cancel_event = cancel_event
            return "codex", "v1"

        def discover(
            self, service, config, cli_path, cli_version, cancel_event, deadline
        ):
            self.discovery_deadline = deadline
            assert cancel_event is self.cancel_event
            return AiModelCatalog(
                provider_key="codex",
                models=[AiModelDescriptor(id="model", model="model", source="live")],
                fetched_at="2026-07-10T00:00:00Z",
                cli_path=cli_path,
                cli_version=cli_version,
                source="live",
            )

    adapter = DeadlineAdapter()
    service = AiModelCatalogService(
        tmp_path / "catalog.json", adapters={"codex": adapter}
    )
    cancel_event = Event()
    started = time.monotonic()

    service.refresh(
        AiProviderConfig(key="codex"), force=True, cancel_event=cancel_event
    )

    assert adapter.identity_deadline is not None
    assert adapter.discovery_deadline == adapter.identity_deadline
    assert (
        started
        < adapter.identity_deadline
        <= started + service.DISCOVERY_TIMEOUT_SECONDS + 0.1
    )
    assert adapter.cancel_event is cancel_event


def test_catalog_cache_ttl_path_version_and_last_good_preservation(tmp_path):
    cache_path = tmp_path / "config" / "ai_model_catalog_cache.json"
    service = AiModelCatalogService(cache_path)
    fetched_at = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    catalog = AiModelCatalog(
        provider_key="codex",
        models=[
            AiModelDescriptor(
                id="id-current",
                model="current-model",
                display_name="현재 모델",
                is_default=True,
                source="live",
            )
        ],
        fetched_at=fetched_at.isoformat().replace("+00:00", "Z"),
        cli_path="C:/Codex/codex.exe",
        cli_version="v1",
        source="live",
    )
    service.save_catalog(catalog)

    cached = service.load_catalog("codex", "saved-but-missing")

    assert cached.source == "cache"
    assert [model.model for model in cached.models] == [
        "current-model",
        "saved-but-missing",
    ]
    assert cached.models[-1].source == "custom"
    assert (
        service.needs_refresh(
            cached,
            "C:/Codex/codex.exe",
            "v1",
            fetched_at + timedelta(hours=5, minutes=59),
        )
        is False
    )
    assert (
        service.needs_refresh(
            cached, "C:/Codex/codex.exe", "v1", fetched_at + timedelta(hours=6)
        )
        is True
    )
    assert service.needs_refresh(cached, "C:/Other/codex.exe", "v1", fetched_at) is True
    assert service.needs_refresh(cached, "C:/Codex/codex.exe", "v2", fetched_at) is True

    with pytest.raises(ValueError, match="빈 모델 목록"):
        service.save_catalog(AiModelCatalog(provider_key="codex", source="live"))
    assert service.load_catalog("codex").default_model == "current-model"
    assert list(cache_path.parent.glob(".*.tmp")) == []


def test_concurrent_catalog_saves_preserve_each_provider(
    tmp_path,
    monkeypatch,
):
    from app.services import ai_model_catalog_service as catalog_module

    cache_path = tmp_path / "config" / "ai_model_catalog_cache.json"
    service = AiModelCatalogService(cache_path)
    real_save_json = catalog_module.save_json
    start_barrier = Barrier(2)
    save_guard = Lock()
    active_saves = 0
    max_active_saves = 0

    def delayed_save_json(path, payload):
        nonlocal active_saves, max_active_saves
        with save_guard:
            active_saves += 1
            max_active_saves = max(max_active_saves, active_saves)
        try:
            time.sleep(0.05)
            real_save_json(path, payload)
        finally:
            with save_guard:
                active_saves -= 1

    monkeypatch.setattr(catalog_module, "save_json", delayed_save_json)

    def catalog(provider_key: str) -> AiModelCatalog:
        return AiModelCatalog(
            provider_key=provider_key,
            models=[
                AiModelDescriptor(
                    id=f"id-{provider_key}",
                    model=f"model-{provider_key}",
                    display_name=provider_key,
                    is_default=True,
                    source="live",
                )
            ],
            fetched_at="2026-07-10T00:00:00Z",
            source="live",
        )

    def save(provider_key: str) -> None:
        start_barrier.wait(timeout=5)
        service.save_catalog(catalog(provider_key))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save, provider) for provider in ("codex", "claude")]
        for future in futures:
            future.result(timeout=5)

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(payload["providers"]) == {"codex", "claude"}
    assert max_active_saves == 1


@pytest.mark.parametrize(
    "case",
    [
        "provider_key_mismatch",
        "duplicate_model",
        "default_reasoning_mismatch",
        "id_whitespace",
        "model_whitespace",
        "fallback_descriptor_source",
    ],
)
def test_semantically_invalid_catalog_cache_falls_back(tmp_path, case):
    first_model = {
        "id": "id-one",
        "model": "model-one",
        "display_name": "모델 하나",
        "supported_reasoning_efforts": ["low"],
        "default_reasoning_effort": "low",
        "input_modalities": ["text"],
        "speed_tiers": [],
        "is_default": True,
        "hidden": False,
        "upgrade": "",
        "availability_message": "",
        "source": "live",
    }
    raw_catalog = {
        "provider_key": "codex",
        "models": [first_model],
        "fetched_at": "2026-07-10T00:00:00Z",
        "cli_path": "C:/Codex/codex.exe",
        "cli_version": "v1",
        "source": "live",
    }
    if case == "provider_key_mismatch":
        raw_catalog["provider_key"] = "gemini"
    elif case == "duplicate_model":
        duplicate = dict(first_model)
        duplicate.update(id="id-two", is_default=False)
        raw_catalog["models"].append(duplicate)
    elif case == "default_reasoning_mismatch":
        first_model["default_reasoning_effort"] = "high"
    elif case == "id_whitespace":
        first_model["id"] = " id-one"
    elif case == "model_whitespace":
        first_model["model"] = "model-one "
    else:
        first_model["source"] = "fallback"

    cache_path = tmp_path / "ai_model_catalog_cache.json"
    cache_path.write_text(
        json.dumps(
            {"version": 1, "providers": {"codex": raw_catalog}}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    service = AiModelCatalogService(cache_path)

    catalog = service.load_catalog("codex")

    assert catalog.provider_key == "codex"
    assert catalog.source == "fallback"
    assert catalog.models == []


def test_invalid_refresh_and_invalid_save_preserve_last_good_cache(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "ai_model_catalog_cache.json"
    service = AiModelCatalogService(cache_path)
    last_good = AiModelCatalog(
        provider_key="codex",
        models=[
            AiModelDescriptor(
                id="id-last-good",
                model="last-good",
                display_name="마지막 정상 모델",
                is_default=True,
                source="live",
            )
        ],
        fetched_at="2026-07-10T00:00:00Z",
        cli_path="codex",
        cli_version="v1",
        source="live",
    )
    service.save_catalog(last_good)
    saved_payload = cache_path.read_text(encoding="utf-8")

    invalid_catalog = AiModelCatalog(
        provider_key="codex",
        models=[
            AiModelDescriptor(
                id="duplicate-id", model="one", is_default=True, source="live"
            ),
            AiModelDescriptor(id="duplicate-id", model="two", source="live"),
        ],
        fetched_at="2026-07-10T01:00:00Z",
        source="live",
    )
    with pytest.raises(ValueError, match="중복|duplicate"):
        service.save_catalog(invalid_catalog)
    assert cache_path.read_text(encoding="utf-8") == saved_payload

    process = _FakeProcess(
        [_response(1, {}), _error_response(2, "model/list unavailable")]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v2")
    )

    with pytest.raises(AiModelCatalogError, match="요청이 실패"):
        service.refresh(
            AiProviderConfig(key="codex"), service.load_catalog("codex"), force=True
        )

    assert cache_path.read_text(encoding="utf-8") == saved_payload
    assert service.load_catalog("codex").default_model == "last-good"


def test_cache_uses_normalized_allowlist_and_roundtrips_upgrade_and_default_modalities(
    tmp_path, monkeypatch
):
    raw_model = _raw_model("normalized-model", is_default=True)
    raw_model.pop("inputModalities")
    raw_model["upgrade"] = None
    raw_model["upgradeInfo"] = {
        "model": "next-model",
        "message": "raw upgrade guidance",
    }
    raw_model["description"] = "RAW PROVIDER DESCRIPTION MUST NOT BE CACHED"
    process = _FakeProcess(
        [_response(1, {}), _response(2, {"data": [raw_model], "nextCursor": None})]
    )
    monkeypatch.setattr(
        "app.services.ai_model_catalog_service.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = AiModelCatalogService(tmp_path / "ai_model_catalog_cache.json")
    monkeypatch.setattr(
        service, "cli_identity", lambda _config, *_args, **_kwargs: ("codex", "v1")
    )

    live = service.discover(AiProviderConfig(key="codex"))

    assert live.models[0].input_modalities == ["text", "image"]
    assert live.models[0].upgrade == "next-model"
    payload_text = service.cache_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    stored_model = payload["providers"]["codex"]["models"][0]
    assert "RAW PROVIDER DESCRIPTION" not in payload_text
    assert set(stored_model) <= {
        "id",
        "model",
        "display_name",
        "supported_reasoning_efforts",
        "default_reasoning_effort",
        "input_modalities",
        "speed_tiers",
        "is_default",
        "hidden",
        "upgrade",
        "availability_message",
        "source",
    }
    assert {
        "description",
        "upgradeInfo",
        "inputModalities",
        "availabilityNux",
        "serviceTiers",
    }.isdisjoint(stored_model)

    cached = service.load_catalog("codex")
    assert cached.models[0].input_modalities == ["text", "image"]
    assert cached.models[0].upgrade == "next-model"


def test_refresh_uses_adapter_identity_and_does_not_replace_fresh_selection(tmp_path):
    class FakeAdapter:
        provider_key = "codex"
        supports_live_discovery = True

        def __init__(self) -> None:
            self.version = "v1"
            self.discovery_calls = 0

        def cli_identity(self, service, config, deadline, cancel_event):
            return "C:/Codex/codex.exe", self.version

        def discover(
            self, service, config, cli_path, cli_version, cancel_event, deadline
        ):
            self.discovery_calls += 1
            return AiModelCatalog(
                provider_key="codex",
                models=[AiModelDescriptor(id="new", model="new", source="live")],
                fetched_at="2026-07-10T00:00:00Z",
                cli_path=cli_path,
                cli_version=cli_version,
                source="live",
            )

    adapter = FakeAdapter()
    service = AiModelCatalogService(
        tmp_path / "catalog.json", adapters={"codex": adapter}
    )
    current = AiModelCatalog(
        provider_key="codex",
        models=[AiModelDescriptor(id="selected", model="selected", source="live")],
        fetched_at=datetime.now(timezone.utc).isoformat(),
        cli_path="C:/Codex/codex.exe",
        cli_version="v1",
        source="cache",
    )

    unchanged = service.refresh(
        AiProviderConfig(key="codex", model="selected"), current
    )
    adapter.version = "v2"
    refreshed = service.refresh(
        AiProviderConfig(key="codex", model="selected"), current
    )

    assert unchanged is not current
    assert unchanged.to_dict() == current.to_dict()
    assert adapter.discovery_calls == 1
    assert [model.model for model in refreshed.models] == ["new", "selected"]
    assert refreshed.models[-1].source == "custom"
    assert refreshed.cli_version == "v2"


def test_fallback_adapters_and_corrupt_cache_keep_app_usable(tmp_path):
    cache_path = tmp_path / "catalog.json"
    cache_path.write_text("{not-json", encoding="utf-8")
    service = AiModelCatalogService(cache_path)

    codex = service.load_catalog("codex", "manual/model")
    claude = service.discover(AiProviderConfig(key="claude", model="claude-custom"))
    gemini = service.discover(AiProviderConfig(key="gemini"))

    assert [model.model for model in codex.models] == ["manual/model"]
    assert codex.models[0].source == "custom"
    assert "claude-custom" in {model.model for model in claude.models}
    assert "gemini-2.5-pro" in {model.model for model in gemini.models}
    assert list(tmp_path.glob("catalog.json.invalid-*"))
