import json

from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.tools.contracts import ToolCall
from jarvis_core.tools.registry import ToolRegistry
from jarvis_core.tools.runtime_info import (
    RUNTIME_INFO_TOOL_NAME,
    register_runtime_info_tool,
)
from jarvis_core.version import JARVIS_VERSION


def strong_profile() -> ModelProfile:
    return ModelProfile(
        name="reasoning_strong",
        provider="packycode",
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )


async def test_runtime_info_returns_only_actual_safe_runtime_snapshot() -> None:
    registry = ToolRegistry()
    definition = register_runtime_info_tool(
        registry,
        chat_profile=strong_profile(),
    )

    result = await registry.execute(
        ToolCall(tool_name=RUNTIME_INFO_TOOL_NAME, arguments={}),
        request_id="request-runtime-info",
    )

    assert definition.name == "system.get_runtime_info"
    assert definition.risk_level == "read_only"
    assert result.success is True
    assert result.error is None
    assert result.data == {
        "jarvis_version": JARVIS_VERSION,
        "runtime_status": "running",
        "chat_profile": "reasoning_strong",
        "provider": "packycode",
        "model": "gpt-5.6-sol",
    }


async def test_runtime_info_accepts_only_empty_arguments() -> None:
    registry = ToolRegistry()
    register_runtime_info_tool(registry, chat_profile=strong_profile())

    result = await registry.execute(
        ToolCall(
            tool_name=RUNTIME_INFO_TOOL_NAME,
            arguments={"include_secrets": True},
        ),
        request_id="request-extra-field",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"


async def test_runtime_info_never_returns_configuration_or_private_state(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "PRIVATE-DEEPSEEK-KEY")
    monkeypatch.setenv("JARVIS_PACKYCODE_API_KEY", "PRIVATE-PACKYCODE-KEY")
    monkeypatch.setenv("JARVIS_DATA_DIR", "C:/PRIVATE/MEMORY/PATH")
    registry = ToolRegistry()
    register_runtime_info_tool(registry, chat_profile=strong_profile())

    result = await registry.execute(
        ToolCall(tool_name=RUNTIME_INFO_TOOL_NAME, arguments={}),
        request_id="request-safe-surface",
    )
    serialized = json.dumps(result.data)

    assert "PRIVATE" not in serialized
    assert "api_key" not in serialized.casefold()
    assert "base_url" not in serialized.casefold()
    assert "memory" not in serialized.casefold()
    assert "path" not in serialized.casefold()
    assert "token" not in serialized.casefold()


async def test_runtime_info_executor_is_repeatable_and_read_only() -> None:
    registry = ToolRegistry()
    register_runtime_info_tool(registry, chat_profile=strong_profile())
    call = ToolCall(tool_name=RUNTIME_INFO_TOOL_NAME, arguments={})

    first = await registry.execute(call, request_id="request-first")
    second = await registry.execute(call, request_id="request-second")

    assert first.success is True
    assert second.success is True
    assert first.data == second.data
    assert first.metadata == {}
    assert second.metadata == {}
