from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from jarvis_core.agent.context import AgentContextBuilder
from jarvis_core.agent.contracts import (
    AgentDecision,
    AgentDecisionModel,
    AgentRuntimeError,
)
from jarvis_core.llm.client import ChatMessage, LLMClient
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.telemetry import FailurePhase, current_request_telemetry
from jarvis_core.tools.contracts import JsonValue, ToolCall, ToolResult
from jarvis_core.tools.registry import ToolRegistry


class AgentResponseRuntime(Protocol):
    def stream_response(
        self,
        messages: Sequence[ChatMessage],
        *,
        current_user_message: str,
        request_id: str,
    ) -> AsyncIterator[str]: ...


class AgentRuntime:
    """One decision, at most one Tool execution, then one final Chat stream."""

    def __init__(
        self,
        *,
        brain: AgentDecisionModel,
        context_builder: AgentContextBuilder,
        registry: ToolRegistry,
        chat_client: LLMClient,
        brain_profile: ModelProfile | None = None,
        chat_profile: ModelProfile | None = None,
    ) -> None:
        self.brain = brain
        self.context_builder = context_builder
        self.registry = registry
        self.chat_client = chat_client
        self.brain_profile = brain_profile
        self.chat_profile = chat_profile

    async def stream_response(
        self,
        messages: Sequence[ChatMessage],
        *,
        current_user_message: str,
        request_id: str,
    ) -> AsyncIterator[str]:
        telemetry = current_request_telemetry()
        try:
            context = self.context_builder.build(
                current_user_message=current_user_message,
                tool_definitions=self.registry.definitions(),
            )
        except Exception as error:
            telemetry.mark_failure(FailurePhase.AGENT_BRAIN)
            raise AgentRuntimeError(
                stage="core_validation",
                code="agent_context_unavailable",
                error_type=type(error).__name__,
            ) from error

        brain_started_at = telemetry.start_agent_brain(profile=self.brain_profile)
        try:
            try:
                decision = await self.brain.decide(context)
            except AgentRuntimeError:
                raise
            except Exception as error:
                raise AgentRuntimeError(
                    stage="provider",
                    code="agent_brain_unavailable",
                    error_type=type(error).__name__,
                ) from error
            if not isinstance(decision, AgentDecision):
                raise AgentRuntimeError(
                    stage="core_validation",
                    code="invalid_agent_decision",
                    error_type="invalid_decision_type",
                )
        except BaseException:
            telemetry.finish_agent_brain(brain_started_at, action="error")
            telemetry.mark_failure(FailurePhase.AGENT_BRAIN)
            raise
        else:
            telemetry.finish_agent_brain(brain_started_at, action=decision.action)

        chat_messages = tuple(dict(message) for message in messages)
        if decision.action == "call_tool":
            if decision.tool_call is None:
                raise AgentRuntimeError(
                    stage="core_validation",
                    code="invalid_agent_decision",
                    error_type="missing_tool_call",
                )
            definition = self.registry.definition(decision.tool_call.tool_name)
            tool_started_at = telemetry.start_tool(
                tool_name=(definition.name if definition is not None else "unknown"),
                risk_level=(
                    definition.risk_level if definition is not None else None
                ),
            )
            try:
                result = await self.registry.execute(
                    decision.tool_call,
                    request_id=request_id,
                )
            except BaseException:
                telemetry.finish_tool(tool_started_at, status="error")
                telemetry.mark_failure(FailurePhase.TOOL_EXECUTION)
                raise
            tool_status = "success" if result.success else _tool_failure_status(result)
            telemetry.finish_tool(tool_started_at, status=tool_status)
            original_message_count = len(chat_messages)
            chat_messages = _with_tool_observation(
                chat_messages,
                decision.tool_call,
                result,
            )
            try:
                observation_contents = tuple(
                    message["content"]
                    for message in chat_messages[original_message_count:]
                )
                telemetry.set_tool_observation_size(
                    chars=sum(len(content) for content in observation_contents),
                    utf8_bytes=sum(
                        len(content.encode("utf-8"))
                        for content in observation_contents
                    ),
                )
            except Exception:  # noqa: BLE001,S110 - telemetry must remain best-effort
                pass

        chat_started_at = telemetry.start_chat(profile=self.chat_profile)
        try:
            async for chunk in self.chat_client.stream_chat(chat_messages):
                if isinstance(chunk, str) and chunk:
                    telemetry.record_chat_first_token(chat_started_at)
                yield chunk
        except BaseException:
            telemetry.fail_chat(chat_started_at)
            raise
        else:
            telemetry.finish_chat(chat_started_at)


def _with_tool_observation(
    messages: Sequence[ChatMessage],
    call: ToolCall,
    result: ToolResult,
) -> tuple[ChatMessage, ...]:
    call_message: ChatMessage = {
        "role": "assistant",
        "content": json.dumps(
            {
                "type": "agent_tool_call",
                "tool_name": call.tool_name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    result_message: ChatMessage = {
        "role": "user",
        "content": json.dumps(
            _result_payload(call.tool_name, result),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    return (
        *(dict(message) for message in messages),
        call_message,
        result_message,
    )


def _result_payload(tool_name: str, result: ToolResult) -> dict[str, JsonValue]:
    error_payload: JsonValue = None
    if result.error is not None:
        error_payload = {
            "code": result.error.code,
            "message": result.error.message,
            "retryable": result.error.retryable,
        }
    return {
        "type": "verified_tool_result",
        "tool_name": tool_name,
        "success": result.success,
        "data": result.data,
        "error": error_payload,
        "metadata": result.metadata,
    }


def _tool_failure_status(result: ToolResult) -> str:
    if result.error is not None and result.error.code in {
        "tool_not_found",
        "tool_invalid_arguments",
        "tool_permission_denied",
    }:
        return "rejected"
    return "error"
