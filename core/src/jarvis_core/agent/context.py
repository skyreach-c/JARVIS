from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from jarvis_core.tools.contracts import ToolDefinition


@dataclass(frozen=True, slots=True)
class AgentRuntimeMetadata:
    jarvis_version: str
    runtime_status: str


@dataclass(frozen=True, slots=True)
class AgentDecisionContext:
    current_user_message: str
    tool_definitions: tuple[ToolDefinition, ...]
    runtime_metadata: AgentRuntimeMetadata


class AgentContextBuilder(Protocol):
    def build(
        self,
        *,
        current_user_message: str,
        tool_definitions: Sequence[ToolDefinition],
    ) -> AgentDecisionContext: ...


class MinimalAgentContextBuilder:
    def __init__(self, *, metadata: AgentRuntimeMetadata) -> None:
        self._metadata = metadata

    def build(
        self,
        *,
        current_user_message: str,
        tool_definitions: Sequence[ToolDefinition],
    ) -> AgentDecisionContext:
        return AgentDecisionContext(
            current_user_message=current_user_message,
            tool_definitions=tuple(tool_definitions),
            runtime_metadata=self._metadata,
        )
