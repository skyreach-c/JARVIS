import asyncio
import json
from collections.abc import AsyncIterator, Sequence

import pytest

from jarvis_core.agent.context import AgentDecisionContext, AgentRuntimeMetadata
from jarvis_core.agent.contracts import AgentDecision, AgentRuntimeError
from jarvis_core.agent.runtime import AgentRuntime
from jarvis_core.llm.client import ChatMessage
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.telemetry import (
    RequestTelemetry,
    bind_request_telemetry,
    reset_request_telemetry,
)
from jarvis_core.tools.contracts import (
    ToolArguments,
    ToolCall,
    ToolError,
    ToolResult,
)
from jarvis_core.tools.registry import ToolRegistry

ORIGINAL_MESSAGES: tuple[ChatMessage, ...] = (
    {"role": "system", "content": "system prompt"},
    {"role": "user", "content": "current request"},
)


class EmptyArguments(ToolArguments):
    pass


class FakeContextBuilder:
    def __init__(self) -> None:
        self.context = AgentDecisionContext(
            current_user_message="sentinel-context",
            tool_definitions=(),
            runtime_metadata=AgentRuntimeMetadata(
                jarvis_version="test",
                runtime_status="running",
            ),
        )
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def build(
        self,
        *,
        current_user_message: str,
        tool_definitions: Sequence[object],
    ) -> AgentDecisionContext:
        self.calls.append((current_user_message, tuple(tool_definitions)))
        return self.context


class FakeBrain:
    def __init__(self, decision: AgentDecision | Exception) -> None:
        self.decision = decision
        self.calls: list[AgentDecisionContext] = []

    async def decide(self, context: AgentDecisionContext) -> AgentDecision:
        self.calls.append(context)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


class RecordingChatClient:
    def __init__(
        self,
        chunks: tuple[str, ...] = ("final ", "answer"),
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.error = error
        self.calls: list[tuple[ChatMessage, ...]] = []

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        self.calls.append(
            tuple(dict(message) for message in messages)  # type: ignore[misc]
        )
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error


class RecordingExecutor:
    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls: list[ToolArguments] = []

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        self.calls.append(arguments)
        return self.result


def registered_runtime(
    *,
    decision: AgentDecision | Exception,
    executor_result: ToolResult | None = None,
    risk_level: str = "read_only",
) -> tuple[
    AgentRuntime,
    FakeContextBuilder,
    FakeBrain,
    RecordingChatClient,
    RecordingExecutor,
]:
    builder = FakeContextBuilder()
    brain = FakeBrain(decision)
    chat = RecordingChatClient()
    registry = ToolRegistry()
    executor = RecordingExecutor(
        executor_result
        or ToolResult(
            success=True,
            data={"runtime_status": "running"},
            error=None,
            metadata={"source": "executor"},
        )
    )
    registry.register(
        name="system.test",
        description="Read test information.",
        arguments_model=EmptyArguments,
        executor=executor,
        risk_level=risk_level,  # type: ignore[arg-type]
        timeout_seconds=1.0,
    )
    return (
        AgentRuntime(
            brain=brain,
            context_builder=builder,
            registry=registry,
            chat_client=chat,
        ),
        builder,
        brain,
        chat,
        executor,
    )


async def collect(runtime: AgentRuntime) -> list[str]:
    return [
        chunk
        async for chunk in runtime.stream_response(
            ORIGINAL_MESSAGES,
            current_user_message="current request",
            request_id="request-agent",
        )
    ]


def test_agent_decision_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="unsupported agent decision action"):
        AgentDecision(
            action="invented",  # type: ignore[arg-type]
            tool_call=None,
        )


async def test_respond_calls_brain_once_tool_zero_and_chat_once() -> None:
    runtime, builder, brain, chat, executor = registered_runtime(
        decision=AgentDecision(action="respond", tool_call=None)
    )

    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry("respond-no-observation", summary_sink=summaries.append)
    telemetry.mark_llm_request(history_turns=0)
    token = bind_request_telemetry(telemetry)
    try:
        chunks = await collect(runtime)
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)

    assert chunks == ["final ", "answer"]
    assert builder.calls[0][0] == "current request"
    assert len(builder.calls[0][1]) == 1
    assert brain.calls == [builder.context]
    assert executor.calls == []
    assert chat.calls == [ORIGINAL_MESSAGES]
    assert "tool_observation_chars" not in summaries[0]
    assert "tool_observation_utf8_bytes" not in summaries[0]


async def test_call_tool_executes_once_and_chat_receives_verified_result() -> None:
    runtime, builder, brain, chat, executor = registered_runtime(
        decision=AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        )
    )

    chunks = await collect(runtime)

    assert chunks == ["final ", "answer"]
    assert len(brain.calls) == 1
    assert len(executor.calls) == 1
    assert len(chat.calls) == 1
    assert len(chat.calls[0]) == len(ORIGINAL_MESSAGES) + 2
    observation = json.loads(chat.calls[0][-1]["content"])
    assert observation == {
        "type": "verified_tool_result",
        "tool_name": "system.test",
        "success": True,
        "data": {"runtime_status": "running"},
        "error": None,
        "metadata": {"source": "executor"},
    }
    assert all("final" not in message["content"] for message in chat.calls[0][:-1])
    assert builder.calls[0][0] == "current request"


@pytest.mark.parametrize(
    ("executor_result", "expected_status"),
    [
        (
            ToolResult(
                success=True,
                data={
                    "relative_path": "资料/观察🙂.txt",
                    "content": "中文🙂正文",
                },
                error=None,
                metadata={},
            ),
            "success",
        ),
        (
            ToolResult(
                success=False,
                data=None,
                error=ToolError(
                    code="safe_failure",
                    message="读取失败🙂",
                    retryable=False,
                ),
                metadata={},
            ),
            "error",
        ),
    ],
)
async def test_tool_observation_telemetry_measures_exact_appended_chat_messages(
    executor_result: ToolResult,
    expected_status: str,
) -> None:
    runtime, _, _, chat, _ = registered_runtime(
        decision=AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        ),
        executor_result=executor_result,
    )
    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry("observation-size", summary_sink=summaries.append)
    telemetry.mark_llm_request(history_turns=0)
    token = bind_request_telemetry(telemetry)
    try:
        assert await collect(runtime) == ["final ", "answer"]
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)

    appended_messages = chat.calls[0][-2:]
    summary = summaries[0]
    assert summary["tool_status"] == expected_status
    assert summary["tool_observation_chars"] == sum(
        len(message["content"]) for message in appended_messages
    )
    assert summary["tool_observation_utf8_bytes"] == sum(
        len(message["content"].encode("utf-8")) for message in appended_messages
    )
    serialized_summary = json.dumps(summary, ensure_ascii=False)
    assert "资料/观察🙂.txt" not in serialized_summary
    assert "中文🙂正文" not in serialized_summary
    assert "读取失败🙂" not in serialized_summary


async def test_tool_observation_telemetry_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime, _, _, chat, _ = registered_runtime(
        decision=AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        )
    )
    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry("broken-observation-size", summary_sink=summaries.append)
    telemetry.mark_llm_request(history_turns=0)
    private_marker = "PRIVATE path/content/payload marker"
    setter_calls = 0

    def broken_setter(*, chars: int, utf8_bytes: int) -> None:
        nonlocal setter_calls
        del chars, utf8_bytes
        setter_calls += 1
        raise RuntimeError(private_marker)

    monkeypatch.setattr(
        telemetry,
        "set_tool_observation_size",
        broken_setter,
        raising=False,
    )
    token = bind_request_telemetry(telemetry)
    try:
        assert await collect(runtime) == ["final ", "answer"]
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)

    assert setter_calls == 1
    assert len(chat.calls) == 1
    assert "tool_observation_chars" not in summaries[0]
    assert "tool_observation_utf8_bytes" not in summaries[0]
    assert private_marker not in caplog.text


@pytest.mark.parametrize(
    ("call", "risk_level", "expected_code"),
    [
        (ToolCall(tool_name="missing", arguments={}), "read_only", "tool_not_found"),
        (
            ToolCall(tool_name="system.test", arguments={"extra": True}),
            "read_only",
            "tool_invalid_arguments",
        ),
        (
            ToolCall(tool_name="system.test", arguments={}),
            "side_effect",
            "tool_permission_denied",
        ),
    ],
)
async def test_rejected_tool_call_is_observed_but_never_executed(
    call: ToolCall,
    risk_level: str,
    expected_code: str,
) -> None:
    runtime, _, brain, chat, executor = registered_runtime(
        decision=AgentDecision(action="call_tool", tool_call=call),
        risk_level=risk_level,
    )

    chunks = await collect(runtime)

    assert chunks == ["final ", "answer"]
    assert len(brain.calls) == 1
    assert executor.calls == []
    assert len(chat.calls) == 1
    observation = json.loads(chat.calls[0][-1]["content"])
    assert observation["success"] is False
    assert observation["error"]["code"] == expected_code


async def test_executor_failure_is_normalized_for_chat_without_success_claim() -> None:
    failure = ToolResult(
        success=False,
        data=None,
        error=ToolError(
            code="safe_executor_failure",
            message="能力未返回已验证结果。",
            retryable=True,
        ),
        metadata={},
    )
    runtime, _, _, chat, executor = registered_runtime(
        decision=AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        ),
        executor_result=failure,
    )

    await collect(runtime)

    assert executor.calls
    observation = json.loads(chat.calls[0][-1]["content"])
    assert observation["success"] is False
    assert observation["error"] == {
        "code": "safe_executor_failure",
        "message": "能力未返回已验证结果。",
        "retryable": True,
    }


async def test_brain_failure_is_terminal_and_does_not_fallback_to_chat() -> None:
    failure = AgentRuntimeError(
        stage="provider",
        code="agent_brain_unavailable",
        error_type="ProviderError",
    )
    runtime, _, brain, chat, executor = registered_runtime(decision=failure)

    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry("brain-failure-no-observation", summary_sink=summaries.append)
    telemetry.mark_llm_request(history_turns=0)
    token = bind_request_telemetry(telemetry)
    try:
        with pytest.raises(AgentRuntimeError) as raised:
            await collect(runtime)
        telemetry.finish(status="error")
    finally:
        reset_request_telemetry(token)

    assert raised.value is failure
    assert len(brain.calls) == 1
    assert chat.calls == []
    assert executor.calls == []
    assert "tool_observation_chars" not in summaries[0]
    assert "tool_observation_utf8_bytes" not in summaries[0]


async def test_fake_builder_is_the_only_context_source_runtime_uses() -> None:
    runtime, builder, brain, _, _ = registered_runtime(
        decision=AgentDecision(action="respond", tool_call=None)
    )

    await collect(runtime)

    assert brain.calls == [builder.context]


async def test_runtime_does_not_modify_upstream_messages() -> None:
    mutable_messages: list[ChatMessage] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "current request"},
    ]
    snapshot = tuple(dict(message) for message in mutable_messages)
    runtime, _, _, _, _ = registered_runtime(
        decision=AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        )
    )

    _ = [
        chunk
        async for chunk in runtime.stream_response(
            mutable_messages,
            current_user_message="current request",
            request_id="request-copy",
        )
    ]

    assert tuple(mutable_messages) == snapshot


async def test_partial_chat_failure_propagates_without_retrying_brain_or_tool() -> None:
    runtime, _, brain, _, executor = registered_runtime(
        decision=AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        )
    )
    failing_chat = RecordingChatClient(
        chunks=("partial",),
        error=RuntimeError("chat failed"),
    )
    runtime.chat_client = failing_chat
    stream = runtime.stream_response(
        ORIGINAL_MESSAGES,
        current_user_message="current request",
        request_id="request-partial",
    )

    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry("partial-chat-failure", summary_sink=summaries.append)
    telemetry.mark_llm_request(history_turns=0)
    token = bind_request_telemetry(telemetry)
    try:
        assert await anext(stream) == "partial"
        with pytest.raises(RuntimeError, match="chat failed"):
            await anext(stream)
        telemetry.mark_failure("provider_stream")
        telemetry.finish(status="error")
    finally:
        reset_request_telemetry(token)

    assert len(brain.calls) == 1
    assert len(executor.calls) == 1
    assert len(failing_chat.calls) == 1
    appended_messages = failing_chat.calls[0][-2:]
    assert summaries[0]["tool_observation_chars"] == sum(
        len(message["content"]) for message in appended_messages
    )
    assert summaries[0]["tool_observation_utf8_bytes"] == sum(
        len(message["content"].encode("utf-8")) for message in appended_messages
    )


class CancellableBrain:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def decide(self, context: AgentDecisionContext) -> AgentDecision:
        del context
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


async def test_cancellation_during_brain_does_not_continue_to_tool_or_chat() -> None:
    builder = FakeContextBuilder()
    brain = CancellableBrain()
    chat = RecordingChatClient()
    registry = ToolRegistry()
    runtime = AgentRuntime(
        brain=brain,
        context_builder=builder,
        registry=registry,
        chat_client=chat,
    )

    async def consume() -> list[str]:
        return [
            chunk
            async for chunk in runtime.stream_response(
                ORIGINAL_MESSAGES,
                current_user_message="cancel me",
                request_id="request-cancel",
            )
        ]

    task = asyncio.create_task(consume())
    await brain.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert brain.cancelled.is_set()
    assert chat.calls == []


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class TimedBrain:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock

    async def decide(self, context: AgentDecisionContext) -> AgentDecision:
        del context
        self.clock.value = 0.100
        return AgentDecision(
            action="call_tool",
            tool_call=ToolCall(tool_name="system.test", arguments={}),
        )


class TimedExecutor:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        del arguments
        self.clock.value = 0.150
        return ToolResult(
            success=True,
            data={"value": "verified"},
            error=None,
            metadata={},
        )


class TimedChatClient:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        del messages
        self.clock.value = 0.200
        yield "visible"
        self.clock.value = 0.300


async def test_runtime_records_separate_brain_tool_and_actual_chat_timings() -> None:
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    registry = ToolRegistry()
    registry.register(
        name="system.test",
        description="Read test information.",
        arguments_model=EmptyArguments,
        executor=TimedExecutor(clock),
        risk_level="read_only",
        timeout_seconds=1.0,
    )
    runtime = AgentRuntime(
        brain=TimedBrain(clock),
        context_builder=FakeContextBuilder(),
        registry=registry,
        chat_client=TimedChatClient(clock),
        brain_profile=ModelProfile(
            name="agent_brain",
            provider="deepseek",
            model="deepseek-v4-flash",
        ),
        chat_profile=ModelProfile(
            name="reasoning_strong",
            provider="packycode",
            model="gpt-5.6-sol",
            reasoning_effort="low",
        ),
    )
    telemetry = RequestTelemetry(
        "timed-runtime",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=0)
    token = bind_request_telemetry(telemetry)
    try:
        assert await collect(runtime) == ["visible"]
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)

    summary = summaries[0]
    assert summary["agent_brain_decision_ms"] == 100.0
    assert summary["tool_execution_ms"] == 50.0
    assert summary["chat_first_token_ms"] == 50.0
    assert summary["chat_stream_ms"] == 100.0
    assert summary["chat_total_ms"] == 150.0
    assert summary["provider_first_token_ms"] == summary["chat_first_token_ms"]
    assert summary["provider_stream_ms"] == summary["chat_stream_ms"]
    assert summary["total_llm_ms"] == summary["chat_total_ms"]
    assert summary["agent_brain_provider"] == "deepseek"
    assert summary["chat_provider"] == "packycode"
    assert summary["provider"] == "packycode"
