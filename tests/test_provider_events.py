from __future__ import annotations

import json

from app.models.ai_models import AiProviderConfig
from app.services.ai_agent_service import (
    PROVIDER_EVENT_FINAL_TEXT,
    PROVIDER_EVENT_PROGRESS,
    PROVIDER_EVENT_REASONING,
    PROVIDER_EVENT_SESSION,
    PROVIDER_EVENT_TOOL,
    ProviderEventAccumulator,
    build_chat_invocation,
    parse_provider_event,
)


def _json_line(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_codex_events_are_classified_without_exposing_work_details() -> None:
    session_line = _json_line(
        {"type": "thread.started", "thread_id": "thread-123"}
    )
    reasoning_line = _json_line(
        {
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "내부 추론"},
        }
    )
    command_line = _json_line(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "rg secret",
                "aggregated_output": "raw tool output",
            },
        }
    )
    answer_line = _json_line(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "최종 답변 후보"},
        }
    )
    completed_line = _json_line({"type": "turn.completed", "usage": {}})

    session = parse_provider_event(session_line)
    reasoning = parse_provider_event(reasoning_line)
    command = parse_provider_event(command_line)
    answer = parse_provider_event(answer_line)
    completed = parse_provider_event(completed_line)

    assert session is not None
    assert session.kind == PROVIDER_EVENT_SESSION
    assert session.session_id == "thread-123"
    assert reasoning is not None and reasoning.kind == PROVIDER_EVENT_REASONING
    assert command is not None and command.kind == PROVIDER_EVENT_TOOL
    assert answer is not None and answer.kind == PROVIDER_EVENT_FINAL_TEXT
    assert answer.text == "최종 답변 후보"
    assert completed is not None
    assert completed.kind == PROVIDER_EVENT_PROGRESS
    assert completed.turn_complete is True

def test_codex_accumulator_emits_only_last_agent_message_after_turn_completed() -> None:
    accumulator = ProviderEventAccumulator("codex")

    assert accumulator.feed_line(
        _json_line(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "파일을 확인하겠습니다."},
            }
        )
    ) == ""
    assert accumulator.feed_line(
        _json_line(
            {
                "type": "item.completed",
                "item": {"type": "reasoning", "text": "중간 분석"},
            }
        )
    ) == ""
    assert accumulator.feed_line(
        _json_line(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "완성된 최종 답변"},
            }
        )
    ) == ""

    assert accumulator.feed_line(_json_line({"type": "turn.completed"})) == (
        "완성된 최종 답변"
    )
    assert accumulator.finish() == ""


def test_codex_accumulator_finish_is_process_exit_fallback() -> None:
    accumulator = ProviderEventAccumulator("codex")
    accumulator.feed_line(
        _json_line(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "종료 직전 답변"},
            }
        )
    )

    assert accumulator.finish() == "종료 직전 답변"
    assert accumulator.finish() == ""


def test_non_answer_structured_and_plain_output_is_not_treated_as_provider_text() -> None:
    unknown_json = _json_line(
        {"type": "item.completed", "item": {"type": "mystery", "value": 1}}
    )

    json_event = parse_provider_event(unknown_json)
    plain_event = parse_provider_event("checking files")

    assert json_event is not None and json_event.kind == PROVIDER_EVENT_PROGRESS
    assert plain_event is not None and plain_event.kind == PROVIDER_EVENT_PROGRESS
    assert json_event.text == ""
    assert plain_event.text == ""


def test_plain_provider_diagnostic_is_bounded_redacted_and_not_an_answer() -> None:
    accumulator = ProviderEventAccumulator("codex")

    assert accumulator.feed_line("x" * 5000 + " token=super-secret") == ""

    assert "super-secret" not in accumulator.diagnostic_text
    assert "[redacted]" in accumulator.diagnostic_text
    assert len(accumulator.diagnostic_text) <= 4000
    assert accumulator.finish() == ""


def test_accumulator_tracks_session_and_error_without_adding_them_to_answer() -> None:
    accumulator = ProviderEventAccumulator("codex")
    assert accumulator.feed_line(
        _json_line({"type": "thread.started", "thread_id": "thread-safe"})
    ) == ""
    assert accumulator.feed_line(
        _json_line(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "미완성 답변"},
            }
        )
    ) == ""
    assert accumulator.feed_line(
        _json_line({"type": "error", "error": {"message": "실행 실패"}})
    ) == ""

    assert accumulator.session_id == "thread-safe"
    assert accumulator.error_text == "실행 실패"
    assert accumulator.finish() == ""


def test_every_provider_prompt_includes_internal_first_public_guardrails() -> None:
    invocation = build_chat_invocation(
        AiProviderConfig(
            key="codex",
            command_path="codex",
            role_prompt="추가 사용자 역할",
        ),
        "장비 작업을 도와줘",
    )

    assert "NetOps 내부 기능 계약과 번들 사용자 가이드를" in invocation.stdin_text
    assert "내부 enum" in invocation.stdin_text
    assert "명시적 동의 전에는 스크립트" in invocation.stdin_text
    assert "추가 사용자 역할" in invocation.stdin_text
