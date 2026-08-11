from __future__ import annotations

from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.tools.contracts import (
    ToolArguments,
    ToolDefinition,
    ToolResult,
)
from jarvis_core.tools.registry import ToolRegistry
from jarvis_core.version import JARVIS_VERSION

RUNTIME_INFO_TOOL_NAME = "system.get_runtime_info"
RUNTIME_INFO_TOOL_TIMEOUT_SECONDS = 1.0


class RuntimeInfoArguments(ToolArguments):
    pass


class RuntimeInfoExecutor:
    def __init__(self, *, chat_profile: ModelProfile) -> None:
        self._chat_profile = chat_profile

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        return ToolResult(
            success=True,
            data={
                "jarvis_version": JARVIS_VERSION,
                "runtime_status": "running",
                "chat_profile": self._chat_profile.name,
                "provider": self._chat_profile.provider,
                "model": self._chat_profile.model,
            },
            error=None,
            metadata={},
        )


def register_runtime_info_tool(
    registry: ToolRegistry,
    *,
    chat_profile: ModelProfile,
) -> ToolDefinition:
    return registry.register(
        name=RUNTIME_INFO_TOOL_NAME,
        description="Read the current JARVIS runtime and active chat model identity.",
        arguments_model=RuntimeInfoArguments,
        executor=RuntimeInfoExecutor(chat_profile=chat_profile),
        risk_level="read_only",
        timeout_seconds=RUNTIME_INFO_TOOL_TIMEOUT_SECONDS,
    )
