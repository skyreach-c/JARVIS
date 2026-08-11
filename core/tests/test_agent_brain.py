import ast
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from jarvis_core.agent.brain import (
    AGENT_BRAIN_MAX_TOKENS,
    StructuredAgentDecisionModel,
)
from jarvis_core.agent.context import AgentDecisionContext, AgentRuntimeMetadata
from jarvis_core.agent.contracts import AgentRuntimeError
from jarvis_core.llm.client import ChatMessage
from jarvis_core.tools.contracts import ToolDefinition


class RecordingStructuredClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls: list[tuple[tuple[ChatMessage, ...], int]] = []

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
    ) -> str:
        self.calls.append(
            (tuple(dict(message) for message in messages), max_tokens)
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def decision_context() -> AgentDecisionContext:
    return AgentDecisionContext(
        current_user_message="原始用户请求：查看当前运行信息",
        tool_definitions=(
            ToolDefinition(
                name="system.get_runtime_info",
                description="Read safe runtime information.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                risk_level="read_only",
            ),
        ),
        runtime_metadata=AgentRuntimeMetadata(
            jarvis_version="v0.5B",
            runtime_status="running",
        ),
    )


async def test_brain_parses_respond_decision_and_sends_minimal_context() -> None:
    client = RecordingStructuredClient(
        '{"action":"respond","tool_name":null,"arguments":{}}'
    )
    brain = StructuredAgentDecisionModel(client)

    decision = await brain.decide(decision_context())

    assert decision.action == "respond"
    assert decision.tool_call is None
    assert len(client.calls) == 1
    messages, max_tokens = client.calls[0]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert max_tokens == AGENT_BRAIN_MAX_TOKENS
    payload = json.loads(messages[1]["content"])
    assert payload == {
        "current_user_message": "原始用户请求：查看当前运行信息",
        "tool_definitions": [
            {
                "name": "system.get_runtime_info",
                "description": "Read safe runtime information.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "risk_level": "read_only",
            }
        ],
        "runtime_metadata": {
            "jarvis_version": "v0.5B",
            "runtime_status": "running",
        },
    }
    assert "session" not in payload
    assert "pinned_memories" not in payload
    assert "messages" not in payload


async def test_brain_parses_one_tool_call_without_executing_it() -> None:
    client = RecordingStructuredClient(
        '{"action":"call_tool","tool_name":"system.get_runtime_info",'
        '"arguments":{}}'
    )
    brain = StructuredAgentDecisionModel(client)

    decision = await brain.decide(decision_context())

    assert decision.action == "call_tool"
    assert decision.tool_call is not None
    assert decision.tool_call.tool_name == "system.get_runtime_info"
    assert decision.tool_call.arguments == {}


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ("not json", "invalid_json"),
        (
            (
                '{"action":"call_tool","tool_name":"system.get_runtime_info",'
                '"arguments":{"value":NaN}}'
            ),
            "invalid_json",
        ),
        (
            (
                '{"action":"call_tool","tool_name":"system.get_runtime_info",'
                '"arguments":{"value":1e999}}'
            ),
            "invalid_schema",
        ),
        (
            (
                '{"action":"call_tool","tool_name":"system.get_runtime_info",'
                '"arguments":{"value":-1e999}}'
            ),
            "invalid_schema",
        ),
        ("[]", "invalid_schema"),
        ('{"tool_name":null,"arguments":{}}', "invalid_schema"),
        (
            '{"action":"invented","tool_name":null,"arguments":{}}',
            "invalid_action",
        ),
        (
            (
                '{"action":"respond","tool_name":"system.get_runtime_info",'
                '"arguments":{}}'
            ),
            "invalid_decision",
        ),
        (
            '{"action":"call_tool","tool_name":null,"arguments":{}}',
            "invalid_decision",
        ),
        (
            (
                '{"action":"call_tool","tool_name":"system.get_runtime_info",'
                '"arguments":{},"extra":true}'
            ),
            "invalid_schema",
        ),
    ],
)
async def test_brain_invalid_output_fails_closed(
    response: str,
    expected_code: str,
) -> None:
    brain = StructuredAgentDecisionModel(RecordingStructuredClient(response))

    with pytest.raises(AgentRuntimeError) as raised:
        await brain.decide(decision_context())

    assert raised.value.stage == "brain_parse"
    assert raised.value.code == expected_code


async def test_brain_provider_failure_is_wrapped_without_raw_text() -> None:
    brain = StructuredAgentDecisionModel(
        RecordingStructuredClient(RuntimeError("PRIVATE provider response"))
    )

    with pytest.raises(AgentRuntimeError) as raised:
        await brain.decide(decision_context())

    assert raised.value.stage == "provider"
    assert raised.value.code == "agent_brain_unavailable"
    assert raised.value.error_type == "RuntimeError"
    assert "PRIVATE" not in str(raised.value)


def test_brain_module_has_no_registry_executor_or_deepseek_dependency() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "src"
        / "jarvis_core"
        / "agent"
        / "brain.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "jarvis_core.llm.deepseek" not in imported_modules
    assert "jarvis_core.tools.registry" not in imported_modules
    assert "jarvis_core.tools.runtime_info" not in imported_modules
    assert ".execute(" not in source
