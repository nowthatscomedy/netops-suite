from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.assistant.capabilities import (
    DEFAULT_FEATURE_CAPABILITY_CATALOG,
    FeatureCapability,
    FeatureCapabilityCatalog,
    SupportLevel,
    all_feature_capabilities,
)
from app.assistant.grounding import (
    AssistantGroundingRequest,
    AssistantGroundingService,
    ConversationGroundingState,
    GroundingAction,
)
from app.assistant.netops_tools import build_netops_tool_registry


@dataclass(frozen=True)
class _GuideEntry:
    id: str
    title: str
    route: str
    keywords: tuple[str, ...] = ()


class _GuideCatalog:
    def __init__(
        self,
        *entries: _GuideEntry,
        markdown: dict[str, str] | None = None,
    ) -> None:
        self._entries = {entry.id: entry for entry in entries}
        self._markdown = dict(markdown or {})

    def resolve(self, guide_id: str) -> _GuideEntry | None:
        return self._entries.get(guide_id)

    @staticmethod
    def matches(entry: _GuideEntry, query: str) -> bool:
        searchable = " ".join((entry.title, *entry.keywords)).casefold()
        return all(term.casefold() in searchable for term in query.split())

    def read_markdown(self, entry: _GuideEntry) -> tuple[str | None, str | None]:
        return self._markdown.get(entry.id), None


def _service() -> AssistantGroundingService:
    guides: dict[str, _GuideEntry] = {}
    for capability in all_feature_capabilities():
        guides.setdefault(
            capability.guide_id,
            _GuideEntry(
                capability.guide_id,
                (
                    "장비 점검 및 백업"
                    if capability.guide_id == "inspector"
                    else capability.public_name
                ),
                capability.route,
                capability.aliases,
            ),
        )
    return AssistantGroundingService(_GuideCatalog(*guides.values()))


def test_default_catalog_covers_every_public_guide_and_exposes_contract_fields() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "docs" / "guide_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ids = {entry["id"] for entry in manifest["guides"]}
    capabilities = all_feature_capabilities()

    assert manifest_ids <= {capability.guide_id for capability in capabilities}
    assert DEFAULT_FEATURE_CAPABILITY_CATALOG.list() == capabilities
    assert len({capability.feature_id for capability in capabilities}) == len(capabilities)
    assert all(capability.title == capability.public_name for capability in capabilities)
    assert all(capability.route and capability.source_paths for capability in capabilities)
    assert all(
        capability.tool_names
        for capability in capabilities
        if capability.support_level is SupportLevel.INTERNAL_EXECUTION
    )
    registry = build_netops_tool_registry()
    missing_tools = {
        tool_name
        for capability in capabilities
        for tool_name in capability.tool_names
        if registry.resolve(tool_name) is None
    }
    assert missing_tools == set()


def test_codex_capabilities_track_the_single_provider_contract_sources() -> None:
    assistant = DEFAULT_FEATURE_CAPABILITY_CATALOG.require("assistant")
    settings = DEFAULT_FEATURE_CAPABILITY_CATALOG.require("settings")

    provider_sources = {
        "app/models/ai_models.py",
        "app/services/ai_agent_service.py",
    }
    assert provider_sources <= set(assistant.source_paths)
    assert "app/services/ai_model_catalog_service.py" in assistant.source_paths
    assert provider_sources <= set(settings.source_paths)
    assert {"Codex", "Codex CLI"} <= set(assistant.aliases)
    assert {"Codex 연결 설정", "Codex 로그인"} <= set(assistant.intents)
    assert "AI 제공자 설정" not in assistant.intents


def test_hostname_log_regression_uses_inspector_variables_and_keeps_follow_up_context() -> None:
    service = _service()
    state = ConversationGroundingState()

    first = service.ground(
        AssistantGroundingRequest("장비 점검/백업 기능을 사용하고 싶어", state)
    )
    second = service.ground(
        AssistantGroundingRequest("각 장비마다 호스트네임이 다른데", state)
    )
    third = service.ground(AssistantGroundingRequest("그 기능으로 해줘", state))

    assert first.feature_id == "inspector"
    assert second.feature_id == "inspector.user-commands"
    assert third.feature_id == "inspector.user-commands"
    assert second.public_name == "장비 점검 및 백업 · 사용자 명령"
    assert second.guide_id == "inspector"
    assert second.route == "inspector"
    assert second.support_level is SupportLevel.PARTIAL
    assert second.action is GroundingAction.PARTIAL_SUPPORT
    assert second.consent_required is True
    assert "{{ new_hostname }}" in second.public_context
    assert "먼저 검증" in second.public_context
    assert "개별 행을 선택할 수 없" in second.public_context
    assert "설정 모드" in second.public_context
    assert "스크립트나 외부 절차는 작성하지 말고" in second.public_context
    assert "custom_commands" not in second.public_context
    assert "inspection_backup" not in second.public_context
    assert "장비 한 대 선택" not in second.public_context
    assert "1행 Excel" not in second.public_context
    response = second.user_response
    assert response.index("지원 범위:") < response.index("화면 절차:")
    assert response.index("화면 절차:") < response.index("성공 확인:")
    assert response.index("성공 확인:") < response.index("중단 기준:")
    assert response.index("중단 기준:") < response.index("현재 한계:")
    assert response.rstrip().endswith("동의한다고 말씀해 주세요.")
    assert "custom_commands" not in response
    assert "inspection_backup" not in response
    assert "응답 원칙" not in response


def test_internal_tool_is_preferred_for_supported_feature() -> None:
    snapshot = _service().ground("여러 IP에 ping 해서 통신 확인해줘")

    assert snapshot.feature_id == "diagnostics.ping"
    assert snapshot.support_level is SupportLevel.INTERNAL_EXECUTION
    assert snapshot.action is GroundingAction.INTERNAL_TOOL
    assert "ping_batch" in snapshot.internal_tool_names
    assert snapshot.consent_required is False
    assert "외부 스크립트보다 이 NetOps 내부 기능" in snapshot.public_context


def test_partial_and_unsupported_alternatives_require_per_request_consent() -> None:
    service = _service()
    state = ConversationGroundingState()

    blocked = service.ground(
        AssistantGroundingRequest("호스트네임을 장비마다 바꿔줘", state)
    )
    allowed = service.ground(
        AssistantGroundingRequest(
            "그 변경 방법을 알려줘",
            state,
            external_alternative_consent=True,
        )
    )
    blocked_again = service.ground(
        AssistantGroundingRequest("그 방법으로 계속해", state)
    )
    unsupported = service.ground("사내 결재 문서를 자동 작성해줘")

    assert blocked.consent_required is True
    assert allowed.action is GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
    assert allowed.consent_required is False
    assert blocked_again.action is GroundingAction.PARTIAL_SUPPORT
    assert blocked_again.consent_required is True
    assert unsupported.action is GroundingAction.REQUIRE_ALTERNATIVE_CONSENT
    assert unsupported.consent_required is True
    assert "NetOps 내부 기능만으로는" in unsupported.user_response
    assert unsupported.user_response.endswith("동의는 이번 요청에만 적용됩니다.")


def test_pending_alternative_consent_is_explicit_single_use_and_clears_on_new_intent() -> None:
    service = _service()
    state = ConversationGroundingState()

    blocked = service.ground(
        AssistantGroundingRequest("스위치 호스트네임을 장비마다 바꿔줘", state)
    )
    assert blocked.consent_required is True
    assert state.pending_alternative_feature_id == "inspector.user-commands"

    consented = service.ground(
        AssistantGroundingRequest("스크립트로 해줘", state)
    )

    assert consented.action is GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
    assert consented.consent_consumed is True
    assert consented.consent_required is False
    assert state.pending_alternative_feature_id == ""

    next_request = service.ground(
        AssistantGroundingRequest("그 방법으로 계속해", state)
    )
    assert next_request.action is GroundingAction.PARTIAL_SUPPORT
    assert next_request.consent_required is True
    assert state.pending_alternative_feature_id == "inspector.user-commands"

    changed_feature = service.ground(
        AssistantGroundingRequest("이제 ping으로 연결 확인해줘", state)
    )
    assert changed_feature.feature_id == "diagnostics.ping"
    assert state.pending_alternative_feature_id == ""


def test_pending_alternative_consent_rejects_negated_or_ambiguous_phrasing() -> None:
    service = _service()

    for message in (
        "스크립트로 하지 마",
        "하지마",
        "하지 말아줘",
        "동의하지 않아",
    ):
        state = ConversationGroundingState()
        blocked = service.ground(
            AssistantGroundingRequest("스위치 호스트네임을 장비마다 바꿔줘", state)
        )
        assert blocked.consent_required is True

        response = service.ground(AssistantGroundingRequest(message, state))

        assert response.action is not GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
        assert response.consent_consumed is False


def test_pending_alternative_consent_accepts_exact_affirmative_response() -> None:
    service = _service()
    state = ConversationGroundingState()
    service.ground(
        AssistantGroundingRequest("스위치 호스트네임을 장비마다 바꿔줘", state)
    )

    consented = service.ground(AssistantGroundingRequest("동의해", state))

    assert consented.action is GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
    assert consented.consent_consumed is True
    assert consented.consent_required is False
    assert state.pending_alternative_feature_id == ""


def test_unsupported_alternative_consent_is_consumed_only_when_pending() -> None:
    service = _service()
    state = ConversationGroundingState()

    blocked = service.ground(
        AssistantGroundingRequest("사내 결재 문서를 대신 작성해줘", state)
    )
    consented = service.ground(AssistantGroundingRequest("예", state))
    no_pending = service.ground(
        AssistantGroundingRequest("예", ConversationGroundingState())
    )

    assert blocked.consent_required is True
    assert consented.action is GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
    assert consented.consent_consumed is True
    assert state.pending_alternative_feature_id == ""
    assert no_pending.action is GroundingAction.REQUIRE_ALTERNATIVE_CONSENT
    assert no_pending.consent_consumed is False


def test_state_keeps_only_three_sanitized_user_utterances_and_can_reset() -> None:
    state = ConversationGroundingState()
    service = _service()
    messages = (
        r"로그는 C:\Users\operator\secret.txt password=hunter2 에 있어",
        "ping 기능 알려줘",
        "장비 점검 기능 알려줘",
        "각 장비마다 호스트네임이 달라",
    )
    for message in messages:
        service.ground(AssistantGroundingRequest(message, state))

    assert len(state.recent_user_utterances) == 3
    history = " ".join(state.recent_user_utterances)
    assert "hunter2" not in history
    assert r"C:\Users" not in history

    reset = service.ground(AssistantGroundingRequest("세션 초기화", state))

    assert reset.reset_performed is True
    assert state.recent_user_utterances == ()
    assert state.active_feature_id == ""
    assert state.pending_alternative_feature_id == ""


def test_explicit_new_feature_replaces_active_feature() -> None:
    service = _service()
    state = ConversationGroundingState()
    service.ground(AssistantGroundingRequest("장비 점검 기능 알려줘", state))

    snapshot = service.ground(
        AssistantGroundingRequest("이제 DNS에서 MX 레코드 조회해줘", state)
    )

    assert snapshot.feature_id == "diagnostics.dns"
    assert state.active_feature_id == "diagnostics.dns"


def test_ambiguous_match_requests_one_clarification_instead_of_guessing() -> None:
    capabilities = FeatureCapabilityCatalog(
        (
            FeatureCapability(
                "alpha",
                "알파 기능",
                "alpha",
                "alpha",
                aliases=("공통 요청",),
            ),
            FeatureCapability(
                "beta",
                "베타 기능",
                "beta",
                "beta",
                aliases=("공통 요청",),
            ),
        )
    )
    catalog = _GuideCatalog(
        _GuideEntry("alpha", "알파 기능", "alpha"),
        _GuideEntry("beta", "베타 기능", "beta"),
    )
    service = AssistantGroundingService(catalog, capabilities)

    snapshot = service.ground("공통 요청을 실행해줘")

    assert snapshot.action is GroundingAction.CLARIFY
    assert snapshot.ambiguous is True
    assert snapshot.candidate_feature_ids == ("alpha", "beta")
    assert "스크립트를 제안하지 말고" in snapshot.public_context
    assert snapshot.user_response == (
        "요청을 알파 기능, 베타 기능 중 하나로 이해할 수 있습니다. "
        "어느 기능을 사용하려는지 알려주세요."
    )
    assert "alpha" not in snapshot.user_response
    assert "beta" not in snapshot.user_response


def test_guide_metadata_participates_in_search_and_link_validation() -> None:
    capability = FeatureCapability(
        "optics",
        "광 모듈 도구",
        "optics-guide",
        "optics",
        aliases=("SFP",),
    )
    catalog = _GuideCatalog(
        _GuideEntry(
            "optics-guide",
            "광 모듈 도구",
            "optics",
            keywords=("광섬유 진단",),
        )
    )
    service = AssistantGroundingService(catalog, (capability,))

    snapshot = service.ground("광섬유 진단을 하고 싶어")

    assert snapshot.feature_id == "optics"
    assert snapshot.guide_available is True
    assert snapshot.guide_id == "optics-guide"
    assert service.catalog_issues() == ()


def test_default_service_loads_bundled_guide_catalog_and_excerpt() -> None:
    service = AssistantGroundingService()

    snapshot = service.ground("장비마다 호스트네임을 변경하는 방법")

    assert snapshot.feature_id == "inspector.user-commands"
    assert snapshot.guide_available is True
    assert snapshot.guide_id == "inspector"
    assert snapshot.guide_excerpt
    assert len(snapshot.guide_excerpt) <= 2001
    assert "장비 점검 및 백업" in snapshot.public_context


def test_safe_bundled_guide_excerpt_is_added_without_title_image_or_local_path() -> None:
    capability = FeatureCapability(
        "sample",
        "샘플 기능",
        "sample-guide",
        "sample",
        aliases=("샘플 실행",),
    )
    entry = _GuideEntry("sample-guide", "샘플 가이드", "sample")
    markdown = """# 샘플 가이드

![화면](assets/screenshot.png)

## 사용 방법

샘플 실행 버튼을 누릅니다. [추가 설명](https://example.invalid/docs)을 확인합니다.
내부 경로 C:\\Users\\operator\\secret.txt
다른 경로 C:/Users/operator/secret.txt
공유 경로 \\\\server\\share\\secret.txt
""" + ("안전한 설명 " * 300)
    service = AssistantGroundingService(
        _GuideCatalog(entry, markdown={entry.id: markdown}),
        (capability,),
    )

    snapshot = service.ground("샘플 실행 방법")

    assert snapshot.guide_excerpt
    assert len(snapshot.guide_excerpt) <= 2001
    assert "# 샘플 가이드" not in snapshot.guide_excerpt
    assert "screenshot.png" not in snapshot.guide_excerpt
    assert "https://" not in snapshot.guide_excerpt
    assert r"C:\Users" not in snapshot.guide_excerpt
    assert "C:/Users" not in snapshot.guide_excerpt
    assert r"\\server\share" not in snapshot.guide_excerpt
    assert "관련 가이드 발췌:" in snapshot.public_context
