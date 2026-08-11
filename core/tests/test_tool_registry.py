import asyncio
import logging
from typing import Literal

import pytest
from pydantic import Field

from jarvis_core.tools.contracts import (
    ToolArguments,
    ToolCall,
    ToolError,
    ToolResult,
)
from jarvis_core.tools.registry import ToolRegistry


class ExampleArguments(ToolArguments):
    count: int = Field(ge=1)


class EmptyArguments(ToolArguments):
    pass


class FloatArguments(ToolArguments):
    value: float


class RecordingExecutor:
    def __init__(self, result: ToolResult | None = None) -> None:
        self.result = result or ToolResult(
            success=True,
            data={"value": "executor-result"},
            error=None,
            metadata={"source": "executor"},
        )
        self.calls: list[ToolArguments] = []

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        self.calls.append(arguments)
        return self.result


def register_example(
    registry: ToolRegistry,
    executor: object,
    *,
    name: str = "system.example",
    risk_level: Literal["read_only", "side_effect", "destructive"] = "read_only",
    timeout_seconds: float = 1.0,
):  # type: ignore[no-untyped-def]
    return registry.register(
        name=name,
        description="Return a deterministic example value.",
        arguments_model=ExampleArguments,
        executor=executor,
        risk_level=risk_level,
        timeout_seconds=timeout_seconds,
    )


def test_registration_name_must_be_unique() -> None:
    registry = ToolRegistry()
    register_example(registry, RecordingExecutor())

    with pytest.raises(ValueError, match="already registered"):
        register_example(registry, RecordingExecutor())


def test_definition_schema_comes_from_registered_arguments_model() -> None:
    registry = ToolRegistry()

    definition = register_example(registry, RecordingExecutor())

    assert definition.name == "system.example"
    assert definition.description == "Return a deterministic example value."
    assert definition.risk_level == "read_only"
    assert definition.input_schema == ExampleArguments.model_json_schema()
    assert registry.definitions() == (definition,)


async def test_unknown_tool_never_calls_any_executor() -> None:
    executor = RecordingExecutor()
    registry = ToolRegistry()
    register_example(registry, executor)

    result = await registry.execute(
        ToolCall(tool_name="system.missing", arguments={"count": 1}),
        request_id="request-unknown",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_not_found"
    assert executor.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"count": 0},
        {"count": "1"},
        {"count": 1, "extra": "forbidden"},
    ],
)
async def test_invalid_or_extra_arguments_never_call_executor(
    arguments: dict[str, object],
) -> None:
    executor = RecordingExecutor()
    registry = ToolRegistry()
    register_example(registry, executor)

    result = await registry.execute(
        ToolCall(tool_name="system.example", arguments=arguments),
        request_id="request-invalid",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert executor.calls == []


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
async def test_non_finite_float_arguments_never_call_executor(value: float) -> None:
    executor = RecordingExecutor()
    registry = ToolRegistry()
    registry.register(
        name="system.float_example",
        description="Validate finite float arguments.",
        arguments_model=FloatArguments,
        executor=executor,
        risk_level="read_only",
        timeout_seconds=1.0,
    )

    result = await registry.execute(
        ToolCall(tool_name="system.float_example", arguments={"value": value}),
        request_id="request-non-finite",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert executor.calls == []


async def test_read_only_tool_executes_with_validated_arguments() -> None:
    executor_result = ToolResult(
        success=True,
        data={"value": "verified"},
        error=None,
        metadata={"source": "executor"},
    )
    executor = RecordingExecutor(executor_result)
    registry = ToolRegistry()
    register_example(registry, executor)

    result = await registry.execute(
        ToolCall(tool_name="system.example", arguments={"count": 2}),
        request_id="request-read-only",
    )

    assert result is executor_result
    assert len(executor.calls) == 1
    assert isinstance(executor.calls[0], ExampleArguments)
    assert executor.calls[0].count == 2


@pytest.mark.parametrize("risk_level", ["side_effect", "destructive"])
async def test_non_read_only_risk_is_rejected_before_executor(
    risk_level: Literal["side_effect", "destructive"],
) -> None:
    executor = RecordingExecutor()
    registry = ToolRegistry()
    register_example(registry, executor, risk_level=risk_level)

    result = await registry.execute(
        ToolCall(tool_name="system.example", arguments={"count": 1}),
        request_id="request-denied",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_permission_denied"
    assert executor.calls == []


class SlowExecutor:
    async def execute(self, arguments: ToolArguments) -> ToolResult:
        await asyncio.sleep(10)
        raise AssertionError("timeout should interrupt the executor")


async def test_timeout_returns_safe_failure() -> None:
    registry = ToolRegistry()
    register_example(registry, SlowExecutor(), timeout_seconds=0.001)

    result = await registry.execute(
        ToolCall(tool_name="system.example", arguments={"count": 1}),
        request_id="request-timeout",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_timeout"
    assert result.error.retryable is True


class RaisingExecutor:
    async def execute(self, arguments: ToolArguments) -> ToolResult:
        raise RuntimeError("PRIVATE exception text from executor")


async def test_executor_exception_is_sanitized_and_does_not_escape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = ToolRegistry()
    register_example(registry, RaisingExecutor())

    with caplog.at_level(logging.WARNING, logger="jarvis_core.tools"):
        result = await registry.execute(
            ToolCall(
                tool_name="system.example",
                arguments={"count": 1, "private": "PRIVATE argument"},
            ),
            request_id="request-exception",
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert "PRIVATE argument" not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="jarvis_core.tools"):
        result = await registry.execute(
            ToolCall(tool_name="system.example", arguments={"count": 1}),
            request_id="request-exception",
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_execution_failed"
    assert "PRIVATE exception text" not in caplog.text
    assert "request-exception" in caplog.text
    assert "system.example" in caplog.text


class CancellableExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


async def test_cancellation_propagates_and_cancels_executor() -> None:
    executor = CancellableExecutor()
    registry = ToolRegistry()
    register_example(registry, executor, timeout_seconds=10)
    task = asyncio.create_task(
        registry.execute(
            ToolCall(tool_name="system.example", arguments={"count": 1}),
            request_id="request-cancel",
        )
    )
    await executor.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert executor.cancelled.is_set()


async def test_registry_never_synthesizes_a_success_result() -> None:
    registry = ToolRegistry()
    executor = RecordingExecutor()
    register_example(registry, executor)

    failures = [
        await registry.execute(
            ToolCall(tool_name="missing", arguments={}),
            request_id="request-failure-1",
        ),
        await registry.execute(
            ToolCall(tool_name="system.example", arguments={}),
            request_id="request-failure-2",
        ),
    ]

    assert all(result.success is False for result in failures)
    assert executor.calls == []


def test_tool_result_requires_consistent_success_and_error_state() -> None:
    with pytest.raises(ValueError, match="successful ToolResult"):
        ToolResult(
            success=True,
            data=None,
            error=ToolError(code="unexpected", message="no", retryable=False),
            metadata={},
        )

    with pytest.raises(ValueError, match="failed ToolResult"):
        ToolResult(success=False, data=None, error=None, metadata={})


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("inf")])
def test_registration_requires_finite_positive_timeout(timeout_seconds: float) -> None:
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="timeout_seconds"):
        register_example(
            registry,
            RecordingExecutor(),
            timeout_seconds=timeout_seconds,
        )
