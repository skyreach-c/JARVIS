from dataclasses import asdict

from jarvis_core.agent.context import (
    AgentRuntimeMetadata,
    MinimalAgentContextBuilder,
)
from jarvis_core.tools.contracts import ToolDefinition


def sample_definition() -> ToolDefinition:
    return ToolDefinition(
        name="system.get_runtime_info",
        description="Read safe runtime information.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        risk_level="read_only",
    )


def test_minimal_context_contains_only_locked_v05b_inputs() -> None:
    metadata = AgentRuntimeMetadata(
        jarvis_version="v0.5B",
        runtime_status="running",
    )
    builder = MinimalAgentContextBuilder(metadata=metadata)

    context = builder.build(
        current_user_message="  用户原文必须完全保留。  ",
        tool_definitions=(sample_definition(),),
    )

    assert context.current_user_message == "  用户原文必须完全保留。  "
    assert context.tool_definitions == (sample_definition(),)
    assert context.runtime_metadata is metadata
    assert set(asdict(context)) == {
        "current_user_message",
        "tool_definitions",
        "runtime_metadata",
    }


def test_minimal_context_does_not_accept_session_memory_or_full_prompt() -> None:
    builder = MinimalAgentContextBuilder(
        metadata=AgentRuntimeMetadata(
            jarvis_version="v0.5B",
            runtime_status="running",
        )
    )

    context = builder.build(
        current_user_message="current-only",
        tool_definitions=(),
    )
    serialized = repr(asdict(context))

    assert "SESSION_SENTINEL" not in serialized
    assert "PINNED_MEMORY_SENTINEL" not in serialized
    assert "PERSONALITY_PROMPT_SENTINEL" not in serialized
    assert not hasattr(context, "session_history")
    assert not hasattr(context, "pinned_memories")
    assert not hasattr(context, "messages")


def test_builder_snapshots_the_tool_definition_sequence() -> None:
    definitions = [sample_definition()]
    builder = MinimalAgentContextBuilder(
        metadata=AgentRuntimeMetadata(
            jarvis_version="v0.5B",
            runtime_status="running",
        )
    )

    context = builder.build(
        current_user_message="runtime info",
        tool_definitions=definitions,
    )
    definitions.clear()

    assert context.tool_definitions == (sample_definition(),)
