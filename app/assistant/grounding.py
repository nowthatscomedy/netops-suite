from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable

from app.assistant.capabilities import (
    DEFAULT_FEATURE_CAPABILITY_CATALOG,
    FeatureCapability,
    FeatureCapabilityCatalog,
    SupportLevel,
)

if TYPE_CHECKING:
    from app.guides.catalog import GuideCatalog, GuideEntry


_RESET_PHRASES = (
    "세션 초기화",
    "대화 초기화",
    "문맥 초기화",
    "컨텍스트 초기화",
    "새 대화",
)
_FOLLOW_UP_PHRASES = (
    "그 기능",
    "이 기능",
    "그걸로",
    "이걸로",
    "그 방법",
    "이 방법",
    "각 장비",
    "장비마다",
    "계속",
)
_ALTERNATIVE_CONSENT_EXACT = {
    "예",
    "네",
    "응",
    "좋아",
    "동의",
    "동의해",
    "동의합니다",
    "그렇게 해줘",
    "그 방법으로 해줘",
    "스크립트로 해줘",
    "스크립트 작성해줘",
    "외부 대안 알려줘",
    "대안 알려줘",
}
_UNSUPPORTED_PENDING_ID = "__unsupported__"
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|token|api[_-]?key|enable_password)\b"
    r"\s*[:=]\s*([^\s,;]+)"
)
_KOREAN_SECRET_RE = re.compile(r"(비밀번호|암호|토큰)\s*[:=]?\s*([^\s,;]+)")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![\w])(?:[a-z]:[\\/])[^\r\n\t]*")
_UNC_PATH_RE = re.compile(r"(?<![\\])\\\\[^\s\\/]+[\\/][^\r\n\t]*")
_POSIX_PATH_RE = re.compile(r"(?<![\w:])/(?:home|users|var|tmp|etc)/[^\s]*", re.I)
_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9a-zA-Z가-힣_+.-]+")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_INTERNAL_MODE_RE = re.compile(r"\b(?:custom_commands|inspection_backup)\b", re.I)


class GroundingAction(str, Enum):
    """Next assistant behavior selected by internal-first routing."""

    INTERNAL_TOOL = "internal_tool"
    UI_GUIDANCE = "ui_guidance"
    PARTIAL_SUPPORT = "partial_support"
    CLARIFY = "clarify"
    REQUIRE_ALTERNATIVE_CONSENT = "require_alternative_consent"
    EXTERNAL_ALTERNATIVE_ALLOWED = "external_alternative_allowed"
    NONE = "none"


class _UnavailableGuideCatalog:
    """Capability-only fallback when the optional guide bundle cannot load."""

    @staticmethod
    def resolve(_guide_id: str) -> None:
        return None

    @staticmethod
    def matches(_entry: object, _query: str) -> bool:
        return False

    @staticmethod
    def read_markdown(_entry: object) -> tuple[None, str]:
        return None, "guide unavailable"


@dataclass(slots=True)
class ConversationGroundingState:
    """Provider-independent conversation context used only for feature routing."""

    recent_user_utterances: tuple[str, ...] = ()
    active_feature_id: str = ""
    pending_alternative_feature_id: str = ""

    def __post_init__(self) -> None:
        self.recent_user_utterances = tuple(
            _sanitize_user_utterance(value)
            for value in self.recent_user_utterances[-3:]
            if str(value or "").strip()
        )
        self.active_feature_id = str(self.active_feature_id or "").strip()
        self.pending_alternative_feature_id = str(
            self.pending_alternative_feature_id or ""
        ).strip()

    def record_user_utterance(self, utterance: str) -> None:
        sanitized = _sanitize_user_utterance(utterance)
        if not sanitized:
            return
        self.recent_user_utterances = (
            *self.recent_user_utterances,
            sanitized,
        )[-3:]

    def activate(self, feature_id: str) -> None:
        self.active_feature_id = str(feature_id or "").strip()

    def require_alternative_consent(self, feature_id: str) -> None:
        self.pending_alternative_feature_id = str(feature_id or "").strip()

    def clear_alternative_consent(self) -> None:
        self.pending_alternative_feature_id = ""

    def reset(self) -> None:
        self.recent_user_utterances = ()
        self.active_feature_id = ""
        self.pending_alternative_feature_id = ""


@dataclass(frozen=True, slots=True)
class AssistantGroundingRequest:
    user_message: str
    state: ConversationGroundingState = field(default_factory=ConversationGroundingState)
    external_alternative_consent: bool = False
    locale: str = "ko"

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_message", str(self.user_message or "").strip())
        if not isinstance(self.state, ConversationGroundingState):
            raise TypeError("AssistantGroundingRequest.state must be ConversationGroundingState.")
        object.__setattr__(self, "external_alternative_consent", bool(self.external_alternative_consent))
        object.__setattr__(self, "locale", str(self.locale or "ko").strip() or "ko")


@dataclass(frozen=True, slots=True)
class AssistantGroundingSnapshot:
    feature_id: str = ""
    public_name: str = ""
    guide_id: str = ""
    guide_title: str = ""
    route: str = ""
    support_level: SupportLevel = SupportLevel.UNSUPPORTED
    action: GroundingAction = GroundingAction.NONE
    public_context: str = ""
    user_response: str = ""
    guide_excerpt: str = ""
    consent_required: bool = False
    ambiguous: bool = False
    candidate_feature_ids: tuple[str, ...] = ()
    candidate_public_names: tuple[str, ...] = ()
    internal_tool_names: tuple[str, ...] = ()
    guide_available: bool = False
    reset_performed: bool = False
    consent_consumed: bool = False
    feature: FeatureCapability | None = None

    @property
    def requires_alternative_consent(self) -> bool:
        return self.consent_required

    @property
    def primary_feature(self) -> FeatureCapability | None:
        return self.feature


@dataclass(frozen=True, slots=True)
class _Match:
    capability: FeatureCapability
    score: float
    current_score: float


class AssistantGroundingService:
    """Resolve user intent against verified NetOps capabilities before an LLM.

    The service is intentionally provider- and UI-independent.  It returns a
    public context block and a deterministic next action; it never executes a
    tool or grants consent on the user's behalf.
    """

    def __init__(
        self,
        catalog: GuideCatalog | None = None,
        capabilities: FeatureCapabilityCatalog | Iterable[FeatureCapability] | None = None,
    ) -> None:
        if catalog is None:
            try:
                from app.guides.catalog import GuideCatalog

                catalog = GuideCatalog.load()
            except (ImportError, OSError, UnicodeError, ValueError, TypeError):
                catalog = _UnavailableGuideCatalog()
        self.guide_catalog = catalog
        if capabilities is None:
            self.capabilities = DEFAULT_FEATURE_CAPABILITY_CATALOG
        elif isinstance(capabilities, FeatureCapabilityCatalog):
            self.capabilities = capabilities
        else:
            self.capabilities = FeatureCapabilityCatalog(capabilities)

    def ground(self, request: AssistantGroundingRequest | str) -> AssistantGroundingSnapshot:
        if isinstance(request, str):
            request = AssistantGroundingRequest(request)
        if not isinstance(request, AssistantGroundingRequest):
            raise TypeError("ground expects AssistantGroundingRequest or str.")

        message, reset_performed = self._consume_reset(request.user_message, request.state)
        if not message:
            if request.user_message and not reset_performed:
                request.state.record_user_utterance(request.user_message)
            return AssistantGroundingSnapshot(
                action=GroundingAction.NONE,
                public_context=(
                    "대화의 NetOps 기능 문맥을 초기화했습니다. 다음 요청부터 새 기능을 탐색합니다."
                    if reset_performed
                    else "요청 내용이 없어 기능을 선택하지 않았습니다."
                ),
                user_response=(
                    "NetOps 기능 문맥을 초기화했습니다. 다음 요청부터 새 기능을 찾아 안내하겠습니다."
                    if reset_performed
                    else "요청 내용을 입력해 주세요."
                ),
                reset_performed=reset_performed,
            )

        pending_feature_id = request.state.pending_alternative_feature_id
        if pending_feature_id and _is_explicit_alternative_consent(message):
            request.state.clear_alternative_consent()
            request.state.record_user_utterance(message)
            if pending_feature_id == _UNSUPPORTED_PENDING_ID:
                request.state.activate("")
                return self._unsupported_snapshot(
                    consent=True,
                    reset_performed=reset_performed,
                    consent_consumed=True,
                )
            pending_capability = self.capabilities.get(pending_feature_id)
            if pending_capability is not None:
                request.state.activate(pending_capability.feature_id)
                return self._snapshot_for_capability(
                    pending_capability,
                    consent=True,
                    reset_performed=reset_performed,
                    consent_consumed=True,
                )

        prior_utterances = request.state.recent_user_utterances
        matches = self._rank_matches(
            message,
            prior_utterances,
            request.state.active_feature_id,
        )
        request.state.record_user_utterance(message)

        if not matches:
            request.state.activate("")
            request.state.clear_alternative_consent()
            snapshot = self._unsupported_snapshot(
                consent=request.external_alternative_consent,
                reset_performed=reset_performed,
            )
            if snapshot.consent_required:
                request.state.require_alternative_consent(_UNSUPPORTED_PENDING_ID)
            return snapshot

        candidates = self._ambiguous_candidates(matches)
        if len(candidates) > 1:
            if pending_feature_id and all(
                candidate.capability.feature_id != pending_feature_id
                for candidate in candidates
            ):
                request.state.clear_alternative_consent()
            return self._ambiguous_snapshot(candidates, reset_performed=reset_performed)

        capability = matches[0].capability
        if (
            request.state.pending_alternative_feature_id
            and request.state.pending_alternative_feature_id != capability.feature_id
        ):
            request.state.clear_alternative_consent()
        if request.external_alternative_consent:
            request.state.clear_alternative_consent()
        request.state.activate(capability.feature_id)
        snapshot = self._snapshot_for_capability(
            capability,
            consent=request.external_alternative_consent,
            reset_performed=reset_performed,
        )
        if snapshot.consent_required:
            request.state.require_alternative_consent(capability.feature_id)
        return snapshot

    def find_capabilities(
        self,
        query: str,
        *,
        state: ConversationGroundingState | None = None,
        limit: int = 5,
    ) -> tuple[FeatureCapability, ...]:
        """Search without mutating conversation state."""

        context = state or ConversationGroundingState()
        matches = self._rank_matches(
            str(query or ""),
            context.recent_user_utterances,
            context.active_feature_id,
        )
        return tuple(match.capability for match in matches[: max(0, int(limit))])

    def catalog_issues(self) -> tuple[str, ...]:
        """Return deterministic guide linkage errors for CI validation."""

        issues: list[str] = []
        for capability in self.capabilities:
            entry = self._guide_entry(capability)
            if entry is None:
                issues.append(
                    f"{capability.feature_id}: linked guide is missing ({capability.guide_id})"
                )
                continue
            if entry.route and entry.route != capability.route:
                issues.append(
                    f"{capability.feature_id}: route differs from guide ({capability.route} != {entry.route})"
                )
        return tuple(issues)

    def _consume_reset(
        self,
        message: str,
        state: ConversationGroundingState,
    ) -> tuple[str, bool]:
        normalized = _normalized(message)
        matched = tuple(phrase for phrase in _RESET_PHRASES if phrase in normalized)
        if not matched:
            return message, False
        state.reset()
        remainder = message
        for phrase in matched:
            remainder = re.sub(re.escape(phrase), " ", remainder, flags=re.IGNORECASE)
        remainder = remainder.strip(" ,.!?그리고\t\r\n")
        return remainder, True

    def _rank_matches(
        self,
        current_message: str,
        prior_utterances: tuple[str, ...],
        active_feature_id: str,
    ) -> list[_Match]:
        current = _normalized(current_message)
        if not current:
            return []
        prior = tuple(_normalized(value) for value in prior_utterances[-3:] if value)
        active = self.capabilities.get(active_feature_id)
        follow_up = any(phrase in current for phrase in _FOLLOW_UP_PHRASES)

        ranked: list[_Match] = []
        for capability in self.capabilities:
            current_score = self._capability_score(capability, current)
            history_score = 0.0
            for index, utterance in enumerate(reversed(prior)):
                history_score += self._capability_score(capability, utterance) * (0.24 / (index + 1))

            score = current_score + history_score
            if active is not None and capability.feature_id == active.feature_id:
                if current_score < 18 or follow_up:
                    score += 24
                else:
                    score += 5
            elif active is not None and _same_feature_family(capability, active):
                score += 8

            # Prefer a specific operation entry when it matches just as well as
            # its broader screen-level parent.
            score += capability.feature_id.count(".") * 2.5
            if current_score >= 10 or (active is not None and score >= 18):
                ranked.append(_Match(capability, score, current_score))

        ranked.sort(key=lambda match: (-match.score, -match.current_score, match.capability.feature_id))
        if not ranked:
            return []
        if ranked[0].current_score < 10 and active is None:
            return []
        return ranked

    def _capability_score(self, capability: FeatureCapability, query: str) -> float:
        score = 0.0
        for term in capability.search_terms:
            score = max(score, _term_score(query, term))

        entry = self._guide_entry(capability)
        if entry is not None:
            guide_score = 0.0
            for term in (entry.title, *entry.keywords):
                guide_score = max(guide_score, _term_score(query, term) * 0.72)
            score = max(score, guide_score)
            try:
                if len(query) >= 4 and self.guide_catalog.matches(entry, query):
                    score = max(score, 16.0)
            except (OSError, UnicodeError, ValueError, TypeError):
                # Guide search is an enhancement. A damaged optional guide must
                # not leak an internal exception into the assistant response.
                pass
        return score

    def _ambiguous_candidates(self, matches: list[_Match]) -> tuple[_Match, ...]:
        top = matches[0]
        if top.score >= 80:
            return (top,)
        choices = [top]
        for match in matches[1:]:
            if len(choices) >= 3:
                break
            if top.score - match.score > 3.5:
                break
            if top.capability.feature_id.startswith(
                f"{match.capability.feature_id}."
            ):
                continue
            if match.capability.route == top.capability.route:
                continue
            choices.append(match)
        return tuple(choices)

    def _snapshot_for_capability(
        self,
        capability: FeatureCapability,
        *,
        consent: bool,
        reset_performed: bool,
        consent_consumed: bool = False,
    ) -> AssistantGroundingSnapshot:
        entry = self._guide_entry(capability)
        guide_id = entry.id if entry is not None else capability.guide_id
        route = entry.route if entry is not None and entry.route else capability.route
        guide_title = entry.title if entry is not None else capability.public_name
        support = capability.support_level
        consent_required = support in {SupportLevel.PARTIAL, SupportLevel.UNSUPPORTED} and not consent

        if consent and support in {SupportLevel.PARTIAL, SupportLevel.UNSUPPORTED}:
            action = GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
        elif support is SupportLevel.INTERNAL_EXECUTION and capability.has_internal_tool:
            action = GroundingAction.INTERNAL_TOOL
        elif support is SupportLevel.PARTIAL:
            action = GroundingAction.PARTIAL_SUPPORT
        else:
            action = GroundingAction.UI_GUIDANCE

        guide_excerpt = self._guide_excerpt(entry)
        return AssistantGroundingSnapshot(
            feature_id=capability.feature_id,
            public_name=capability.public_name,
            guide_id=guide_id,
            guide_title=guide_title,
            route=route,
            support_level=support,
            action=action,
            public_context=self._public_context(
                capability,
                guide_title,
                guide_excerpt=guide_excerpt,
                consent=consent,
            ),
            user_response=self._user_response(
                capability,
                guide_title,
                consent=consent,
            ),
            guide_excerpt=guide_excerpt,
            consent_required=consent_required,
            candidate_feature_ids=(capability.feature_id,),
            candidate_public_names=(capability.public_name,),
            internal_tool_names=capability.tool_names,
            guide_available=entry is not None,
            reset_performed=reset_performed,
            consent_consumed=consent_consumed,
            feature=capability,
        )

    def _unsupported_snapshot(
        self,
        *,
        consent: bool,
        reset_performed: bool,
        consent_consumed: bool = False,
    ) -> AssistantGroundingSnapshot:
        action = (
            GroundingAction.EXTERNAL_ALTERNATIVE_ALLOWED
            if consent
            else GroundingAction.REQUIRE_ALTERNATIVE_CONSENT
        )
        context = (
            "확인된 NetOps 내부 기능과 직접 연결되지 않았습니다. "
            + (
                "사용자가 이번 요청에 한해 외부 절차나 스크립트 대안을 허용했습니다. "
                "그래도 적용 대상, 위험과 되돌리는 방법을 먼저 설명합니다."
                if consent
                else "스크립트나 외부 절차를 작성하지 말고, 대안을 원하는지 먼저 확인합니다. "
                "동의는 이번 요청에만 적용하며 다음 요청으로 승계하지 않습니다."
            )
        )
        return AssistantGroundingSnapshot(
            support_level=SupportLevel.UNSUPPORTED,
            action=action,
            public_context=context,
            user_response=(
                "현재 확인된 NetOps 내부 기능만으로는 이 요청을 처리할 수 없습니다. "
                + (
                    "이번 요청에 대한 외부 대안 사용 동의를 확인했습니다. 적용 범위와 위험, 되돌리는 방법을 먼저 확인해 주세요."
                    if consent
                    else "외부 절차나 스크립트 대안을 안내해도 될까요? 동의는 이번 요청에만 적용됩니다."
                )
            ),
            consent_required=not consent,
            reset_performed=reset_performed,
            consent_consumed=consent_consumed,
        )

    def _ambiguous_snapshot(
        self,
        candidates: tuple[_Match, ...],
        *,
        reset_performed: bool,
    ) -> AssistantGroundingSnapshot:
        capabilities = tuple(candidate.capability for candidate in candidates)
        names = tuple(capability.public_name for capability in capabilities)
        context = (
            "요청과 연결될 수 있는 NetOps 기능이 여러 개입니다: "
            + ", ".join(names)
            + ". 기능을 추측하거나 스크립트를 제안하지 말고, 사용자가 원하는 기능을 한 번만 확인합니다."
        )
        return AssistantGroundingSnapshot(
            action=GroundingAction.CLARIFY,
            public_context=context,
            user_response=(
                f"요청을 {', '.join(names)} 중 하나로 이해할 수 있습니다. "
                "어느 기능을 사용하려는지 알려주세요."
            ),
            ambiguous=True,
            candidate_feature_ids=tuple(capability.feature_id for capability in capabilities),
            candidate_public_names=names,
            reset_performed=reset_performed,
        )

    def _guide_entry(self, capability: FeatureCapability) -> GuideEntry | None:
        try:
            return self.guide_catalog.resolve(capability.guide_id)
        except (AttributeError, OSError, UnicodeError, ValueError, TypeError):
            return None

    def _guide_excerpt(self, entry: GuideEntry | None, maximum: int = 2000) -> str:
        if entry is None:
            return ""
        reader = getattr(self.guide_catalog, "read_markdown", None)
        if not callable(reader):
            return ""
        try:
            result = reader(entry)
            markdown = result[0] if isinstance(result, tuple) else result
        except (OSError, UnicodeError, ValueError, TypeError):
            return ""
        if not isinstance(markdown, str) or not markdown.strip():
            return ""

        cleaned_lines: list[str] = []
        skipped_document_title = False
        in_front_matter = False
        for index, raw_line in enumerate(markdown.splitlines()):
            line = raw_line.strip()
            if index == 0 and line == "---":
                in_front_matter = True
                continue
            if in_front_matter:
                if line == "---":
                    in_front_matter = False
                continue
            if not skipped_document_title and line.startswith("# "):
                skipped_document_title = True
                continue
            line = _MARKDOWN_IMAGE_RE.sub("", line)
            line = _MARKDOWN_LINK_RE.sub(r"\1", line)
            line = _HTML_TAG_RE.sub("", line)
            line = re.sub(r"^#{2,6}\s+", "", line)
            line = _INTERNAL_MODE_RE.sub("[내부 값]", line)
            line = _sanitize_user_utterance(line)
            if line or (cleaned_lines and cleaned_lines[-1]):
                cleaned_lines.append(line)

        excerpt = "\n".join(cleaned_lines).strip()
        if len(excerpt) <= maximum:
            return excerpt
        shortened = excerpt[:maximum].rsplit(" ", 1)[0].rstrip()
        return f"{shortened or excerpt[:maximum].rstrip()}…"

    @staticmethod
    def _public_context(
        capability: FeatureCapability,
        guide_title: str,
        *,
        guide_excerpt: str,
        consent: bool,
    ) -> str:
        lines = [
            "[NetOps 내부 기능 우선 안내]",
            f"기능: {capability.public_name}",
            f"현재 지원 범위: {capability.support_level.public_label}",
        ]
        _append_section(lines, "가능한 작업", capability.operations)
        _append_section(lines, "필요 입력", capability.inputs)
        _append_section(lines, "정확한 화면 절차", capability.steps)
        _append_section(lines, "성공 확인", capability.success_checks)
        _append_section(lines, "실패 시 중단", capability.stop_conditions)
        _append_section(lines, "제약사항", capability.constraints)
        if guide_title:
            lines.append(f"관련 가이드: {guide_title}")
        if guide_excerpt:
            lines.extend(("관련 가이드 발췌:", guide_excerpt))

        if capability.support_level is SupportLevel.INTERNAL_EXECUTION:
            lines.append("응답 원칙: 외부 스크립트보다 이 NetOps 내부 기능과 기존 승인 절차를 먼저 사용합니다.")
        elif capability.support_level is SupportLevel.UI_GUIDANCE:
            lines.append("응답 원칙: 화면에 실제로 표시되는 공개 명칭으로 내부 기능 사용 절차를 먼저 안내합니다.")
        elif capability.support_level is SupportLevel.PARTIAL:
            lines.append("응답 원칙: 지원되는 내부 절차와 지원되지 않는 한계를 함께 설명하며 실행 가능하다고 과장하지 않습니다.")
            if consent:
                lines.append("사용자는 이번 요청에 한해 외부 절차 대안을 허용했습니다. 위험과 되돌리는 방법을 먼저 설명합니다.")
            else:
                lines.append("스크립트나 외부 절차는 작성하지 말고 사용자가 이번 요청에 동의하는지 먼저 확인합니다.")
        else:
            lines.append("응답 원칙: 내부 지원이 없음을 알리고 스크립트나 외부 절차를 제안하기 전에 동의를 받습니다.")
        lines.append("표현 원칙: 내부 코드, 내부 경로, 존재하지 않는 버튼이나 확인되지 않은 실행 능력을 노출하지 않습니다.")
        return "\n".join(lines)

    @staticmethod
    def _user_response(
        capability: FeatureCapability,
        guide_title: str,
        *,
        consent: bool,
    ) -> str:
        opening = {
            SupportLevel.INTERNAL_EXECUTION: f"{capability.public_name} 기능에서 내부 실행을 지원합니다.",
            SupportLevel.UI_GUIDANCE: f"{capability.public_name} 기능의 화면 절차를 안내할 수 있습니다.",
            SupportLevel.PARTIAL: f"{capability.public_name} 기능으로 일부 작업을 지원합니다.",
            SupportLevel.UNSUPPORTED: f"{capability.public_name} 기능에서는 이 작업을 지원하지 않습니다.",
        }[capability.support_level]
        lines = [opening]
        if capability.operations:
            lines.append("지원 범위: " + ", ".join(capability.operations))
        if capability.steps:
            lines.append("화면 절차:")
            lines.extend(
                f"{index}. {step}"
                for index, step in enumerate(capability.steps, start=1)
            )
        if capability.success_checks:
            lines.append("성공 확인: " + " ".join(capability.success_checks))
        if capability.stop_conditions:
            lines.append("중단 기준: " + " ".join(capability.stop_conditions))
        if capability.constraints:
            lines.append("현재 한계: " + " ".join(capability.constraints))
        if guide_title:
            lines.append(f"관련 가이드: {guide_title}")

        if capability.support_level is SupportLevel.PARTIAL:
            if consent:
                lines.append(
                    "이번 요청에 대한 외부 대안 사용 동의를 확인했습니다. "
                    "적용 범위와 위험, 되돌리는 방법을 먼저 확인해 주세요."
                )
            else:
                lines.append(
                    "외부 대안이나 스크립트가 필요하면 이번 요청에 한해 동의한다고 말씀해 주세요."
                )
        elif capability.support_level is SupportLevel.UNSUPPORTED:
            lines.append(
                "외부 대안이 필요하면 이번 요청에 한해 동의한다고 말씀해 주세요."
            )
        return "\n".join(lines)


def _append_section(lines: list[str], title: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    lines.append(f"{title}:")
    lines.extend(f"- {value}" for value in values)


def _normalized(value: str) -> str:
    return _SPACE_RE.sub(
        " ", unicodedata.normalize("NFKC", str(value or "")).casefold()
    ).strip()


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(_normalized(value)) if token}


def _term_score(query: str, raw_term: str) -> float:
    term = _normalized(raw_term)
    if not term or not query:
        return 0.0
    if query == term:
        return 120.0 + min(len(term), 20)
    if term in query:
        if len(term) == 1:
            return 0.0
        return 34.0 + min(len(term), 20)
    query_tokens = _tokens(query)
    term_tokens = _tokens(term)
    if not query_tokens or not term_tokens:
        return 0.0
    overlap = query_tokens & term_tokens
    if overlap == term_tokens:
        return 24.0 + min(sum(len(token) for token in overlap), 12)
    if overlap and len(term_tokens) > 1:
        return 8.0 * (len(overlap) / len(term_tokens))
    return 0.0


def _same_feature_family(left: FeatureCapability, right: FeatureCapability) -> bool:
    if left.route == right.route or left.guide_id == right.guide_id:
        return True
    left_root = left.feature_id.split(".", 1)[0]
    right_root = right.feature_id.split(".", 1)[0]
    return left_root == right_root


def _is_explicit_alternative_consent(value: str) -> bool:
    normalized = _normalized(value).strip(" .,!?")
    return normalized in _ALTERNATIVE_CONSENT_EXACT


def _sanitize_user_utterance(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[민감 정보 제거]", text)
    text = _KOREAN_SECRET_RE.sub(lambda match: f"{match.group(1)}=[민감 정보 제거]", text)
    text = _WINDOWS_PATH_RE.sub("[로컬 경로]", text)
    text = _UNC_PATH_RE.sub("[로컬 경로]", text)
    text = _POSIX_PATH_RE.sub("[로컬 경로]", text)
    return _SPACE_RE.sub(" ", text).strip()


__all__ = [
    "AssistantGroundingRequest",
    "AssistantGroundingService",
    "AssistantGroundingSnapshot",
    "ConversationGroundingState",
    "GroundingAction",
]
