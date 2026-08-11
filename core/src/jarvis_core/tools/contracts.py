from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

type JsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list[JsonValue]
    | dict[str, JsonValue]
)
type ToolRiskLevel = Literal["read_only", "side_effect", "destructive"]


class ToolArguments(BaseModel):
    """Strict base model for every registered Tool's arguments."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, JsonValue]
    risk_level: ToolRiskLevel


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ToolResult:
    success: bool
    data: JsonValue | None
    error: ToolError | None
    metadata: dict[str, JsonValue]

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError("successful ToolResult must not contain an error")
        if not self.success and self.error is None:
            raise ValueError("failed ToolResult must contain an error")
        if not _is_json_value(self.data):
            raise ValueError("ToolResult data must be JSON-compatible")
        if not _is_json_value(self.metadata):
            raise ValueError("ToolResult metadata must be JSON-compatible")


class ToolExecutor(Protocol):
    async def execute(self, arguments: ToolArguments) -> ToolResult: ...


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )
    return False
