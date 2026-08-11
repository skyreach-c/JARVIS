from __future__ import annotations

import asyncio
import os
import platform

from jarvis_core.tools.contracts import (
    ToolArguments,
    ToolDefinition,
    ToolError,
    ToolResult,
)
from jarvis_core.tools.registry import ToolRegistry

OS_INFO_TOOL_NAME = "system.get_os_info"
OS_INFO_TOOL_TIMEOUT_SECONDS = 1.0


class OsInfoArguments(ToolArguments):
    pass


class OsInfoExecutor:
    async def execute(self, arguments: ToolArguments) -> ToolResult:
        try:
            data = await asyncio.to_thread(_read_os_snapshot)
        except Exception:  # noqa: BLE001 - system probes must fail closed
            return ToolResult(
                success=False,
                data=None,
                error=ToolError(
                    code="tool_execution_failed",
                    message="Operating-system information could not be read safely.",
                    retryable=True,
                ),
                metadata={},
            )

        return ToolResult(
            success=True,
            data=data,
            error=None,
            metadata={},
        )


def _read_os_snapshot() -> dict[str, str | int | None]:
    return {
        "os_family": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
    }


def register_os_info_tool(registry: ToolRegistry) -> ToolDefinition:
    return registry.register(
        name=OS_INFO_TOOL_NAME,
        description="Read a safe snapshot of the host operating system.",
        arguments_model=OsInfoArguments,
        executor=OsInfoExecutor(),
        risk_level="read_only",
        timeout_seconds=OS_INFO_TOOL_TIMEOUT_SECONDS,
    )
