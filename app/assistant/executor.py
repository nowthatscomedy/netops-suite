from __future__ import annotations

from typing import Any

from app.assistant.audit import AuditLogger
from app.assistant.models import PolicyContext, PolicyDecision, ToolCallRequest, ToolResult
from app.assistant.policy import PolicyEvaluator
from app.assistant.registry import ToolRegistry


class ToolExecutor:
    def __init__(
        self,
        state: Any,
        registry: ToolRegistry,
        evaluator: PolicyEvaluator | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.state = state
        self.registry = registry
        self.evaluator = evaluator or PolicyEvaluator(registry)
        self.audit_logger = audit_logger

    def evaluate(self, call: ToolCallRequest, context: PolicyContext | None = None) -> PolicyDecision:
        decision = self.evaluator.evaluate(call, context)
        self._audit(decision)
        return decision

    def execute(
        self,
        call: ToolCallRequest,
        context: PolicyContext | None = None,
        *,
        cancel_event: Any | None = None,
    ) -> tuple[PolicyDecision, ToolResult | None]:
        decision = self.evaluator.evaluate(call, context)
        if not decision.allowed:
            self._audit(decision)
            return decision, None

        try:
            handler = self.registry.handler_for(call.tool_name)
        except LookupError as exc:
            blocked = PolicyDecision.block(
                "Registered tool has no execution handler.",
                permission_class=decision.permission_class,
                tool_name=call.tool_name,
            )
            result = ToolResult.failed(str(exc))
            self._audit(blocked, result)
            return blocked, result

        try:
            arguments = dict(call.arguments)
            if cancel_event is not None:
                # Internal cooperative-cancellation metadata is added only after
                # policy/schema validation, so it is never part of the public
                # tool contract or the audited user request.
                arguments["_cancel_event"] = cancel_event
            result = handler(self.state, arguments)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult.failed(f"Tool execution failed: {exc}")

        self._audit(decision, result)
        return decision, result

    def _audit(self, decision: PolicyDecision, result: ToolResult | None = None) -> None:
        if self.audit_logger is None:
            return
        recorder = getattr(self.audit_logger, "record_decision", None)
        if callable(recorder):
            recorder(decision, result=result)
