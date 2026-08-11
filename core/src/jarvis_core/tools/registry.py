from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

from pydantic import ValidationError

from jarvis_core.tools.contracts import (
    ToolArguments,
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolExecutor,
    ToolResult,
    ToolRiskLevel,
)

LOGGER = logging.getLogger("jarvis_core.tools")


@dataclass(frozen=True, slots=True)
class _ToolRegistration:
    definition: ToolDefinition
    arguments_model: type[ToolArguments]
    executor: ToolExecutor
    timeout_seconds: float


class ToolRegistry:
    """Binds Tool metadata, strict validation, policy, and execution."""

    def __init__(self) -> None:
        self._registrations: dict[str, _ToolRegistration] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        arguments_model: type[ToolArguments],
        executor: ToolExecutor,
        risk_level: ToolRiskLevel,
        timeout_seconds: float,
    ) -> ToolDefinition:
        if name in self._registrations:
            raise ValueError(f"tool already registered: {name}")
        if not name.strip():
            raise ValueError("tool name must not be empty")
        if not description.strip():
            raise ValueError("tool description must not be empty")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if not issubclass(arguments_model, ToolArguments):
            raise TypeError("arguments_model must inherit ToolArguments")

        definition = ToolDefinition(
            name=name,
            description=description,
            input_schema=arguments_model.model_json_schema(),
            risk_level=risk_level,
        )
        self._registrations[name] = _ToolRegistration(
            definition=definition,
            arguments_model=arguments_model,
            executor=executor,
            timeout_seconds=float(timeout_seconds),
        )
        return definition

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            registration.definition
            for registration in self._registrations.values()
        )

    def definition(self, name: str) -> ToolDefinition | None:
        registration = self._registrations.get(name)
        return registration.definition if registration is not None else None

    async def execute(self, call: ToolCall, *, request_id: str) -> ToolResult:
        registration = self._registrations.get(call.tool_name)
        if registration is None:
            return _failure(
                code="tool_not_found",
                message="请求的能力当前不可用。",
                retryable=False,
            )

        if registration.definition.risk_level != "read_only":
            return _failure(
                code="tool_permission_denied",
                message="当前版本不允许执行有副作用的能力。",
                retryable=False,
            )

        try:
            arguments = registration.arguments_model.model_validate(
                call.arguments,
                strict=True,
                extra="forbid",
            )
        except ValidationError:
            return _failure(
                code="tool_invalid_arguments",
                message="能力调用参数无效，因此没有执行。",
                retryable=False,
            )

        try:
            async with asyncio.timeout(registration.timeout_seconds):
                result = await registration.executor.execute(arguments)
            if not isinstance(result, ToolResult):
                raise TypeError("executor returned an invalid result type")
            return result
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            _log_failure(
                request_id=request_id,
                registration=registration,
                code="tool_timeout",
                error_type="TimeoutError",
            )
            return _failure(
                code="tool_timeout",
                message="能力执行超时，没有确认任何结果。",
                retryable=True,
            )
        except Exception as error:  # noqa: BLE001 - executor boundary must fail closed
            _log_failure(
                request_id=request_id,
                registration=registration,
                code="tool_execution_failed",
                error_type=type(error).__name__,
            )
            return _failure(
                code="tool_execution_failed",
                message="能力执行失败，没有确认任何结果。",
                retryable=True,
            )


def _failure(*, code: str, message: str, retryable: bool) -> ToolResult:
    return ToolResult(
        success=False,
        data=None,
        error=ToolError(code=code, message=message, retryable=retryable),
        metadata={},
    )


def _log_failure(
    *,
    request_id: str,
    registration: _ToolRegistration,
    code: str,
    error_type: str,
) -> None:
    LOGGER.warning(
        "Tool execution failed request_id=%s tool_name=%s risk_level=%s "
        "code=%s error_type=%s",
        request_id,
        registration.definition.name,
        registration.definition.risk_level,
        code,
        error_type,
    )
