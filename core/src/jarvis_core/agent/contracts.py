from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from jarvis_core.tools.contracts import ToolCall

if TYPE_CHECKING:
    from jarvis_core.agent.context import AgentDecisionContext

type AgentDecisionAction = Literal["respond", "call_tool"]
type AgentRuntimeStage = Literal[
    "provider",
    "brain_parse",
    "core_validation",
    "tool_execution",
    "chat",
]


@dataclass(frozen=True, slots=True)
class AgentDecision:
    action: AgentDecisionAction
    tool_call: ToolCall | None

    def __post_init__(self) -> None:
        if self.action not in {"respond", "call_tool"}:
            raise ValueError("unsupported agent decision action")
        if self.action == "respond" and self.tool_call is not None:
            raise ValueError("respond decision must not contain a ToolCall")
        if self.action == "call_tool" and self.tool_call is None:
            raise ValueError("call_tool decision must contain a ToolCall")


class AgentDecisionModel(Protocol):
    async def decide(self, context: AgentDecisionContext) -> AgentDecision: ...


class AgentRuntimeError(Exception):
    def __init__(
        self,
        *,
        stage: AgentRuntimeStage,
        code: str,
        error_type: str,
    ) -> None:
        super().__init__(
            f"agent runtime failed: stage={stage} code={code} "
            f"error_type={error_type}"
        )
        self.stage = stage
        self.code = code
        self.error_type = error_type
