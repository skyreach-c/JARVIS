from pathlib import Path

import pytest

from jarvis_core.__main__ import build_conversation
from jarvis_core.agent.brain import StructuredAgentDecisionModel
from jarvis_core.agent.runtime import AgentRuntime
from jarvis_core.conversation import LLMConversation
from jarvis_core.llm.config import find_project_root
from jarvis_core.llm.deepseek import DeepSeekClient, DeepSeekStructuredClient
from jarvis_core.llm.packycode import PackyCodeResponsesClient
from jarvis_core.memory_router import SemanticMemoryIntentRouter
from jarvis_core.memory_store import SQLiteMemoryStore
from jarvis_core.personality import JARVIS_PERSONALITY_INSTRUCTIONS
from jarvis_core.runtime_capabilities import CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS
from jarvis_core.tools.contracts import ToolCall
from jarvis_core.tools.filesystem import (
    GET_METADATA_TOOL_NAME,
    LIST_DIRECTORY_TOOL_NAME,
)
from jarvis_core.tools.runtime_info import RUNTIME_INFO_TOOL_NAME
from jarvis_core.tools.system_info import OS_INFO_TOOL_NAME
from jarvis_core.tools.text_files import READ_TEXT_TOOL_NAME


async def test_production_conversation_uses_deepseek_without_cwd_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "disabled")
    monkeypatch.setenv("JARVIS_CHAT_PROFILE", "chat_default")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cwd-only.txt").write_text("wrong root", encoding="utf-8")
    data_dir = tmp_path / "jarvis-data"

    conversation = build_conversation(data_dir=data_dir)

    assert isinstance(conversation, LLMConversation)
    runtime = conversation.agent_runtime
    assert isinstance(runtime, AgentRuntime)
    assert isinstance(runtime.chat_client, DeepSeekClient)
    assert runtime.chat_client.settings.api_key == "test-key"
    assert runtime.chat_client.settings.base_url == "https://api.deepseek.com"
    assert runtime.chat_client.settings.model == "deepseek-v4-flash"
    assert runtime.chat_client.settings.thinking_mode == "disabled"
    assert isinstance(runtime.brain, StructuredAgentDecisionModel)
    assert isinstance(runtime.brain.client, DeepSeekStructuredClient)
    assert runtime.brain_profile.name == "agent_brain"
    assert runtime.brain_profile.provider == "deepseek"
    assert runtime.chat_profile.name == "chat_default"
    assert tuple(definition.name for definition in runtime.registry.definitions()) == (
        RUNTIME_INFO_TOOL_NAME,
        OS_INFO_TOOL_NAME,
        LIST_DIRECTORY_TOOL_NAME,
        GET_METADATA_TOOL_NAME,
        READ_TEXT_TOOL_NAME,
    )
    project_root = find_project_root(Path(__file__))
    assert project_root.is_absolute()
    root_listing = await runtime.registry.execute(
        ToolCall(
            tool_name=LIST_DIRECTORY_TOOL_NAME,
            arguments={"relative_path": ".", "limit": 100},
        ),
        request_id="production-project-root-listing",
    )
    project_metadata = await runtime.registry.execute(
        ToolCall(
            tool_name=GET_METADATA_TOOL_NAME,
            arguments={"relative_path": "core/pyproject.toml"},
        ),
        request_id="production-project-root-metadata",
    )
    project_text = await runtime.registry.execute(
        ToolCall(
            tool_name=READ_TEXT_TOOL_NAME,
            arguments={
                "relative_path": "core/pyproject.toml",
                "start_line": 1,
                "max_lines": 3,
            },
        ),
        request_id="production-project-root-text",
    )
    assert root_listing.success is True
    assert root_listing.data is not None
    listed_names = {entry["name"] for entry in root_listing.data["entries"]}
    assert "core" in listed_names
    assert "cwd-only.txt" not in listed_names
    assert project_metadata.success is True
    assert project_metadata.data is not None
    assert project_metadata.data["relative_path"] == "core/pyproject.toml"
    assert project_metadata.data["exists"] is True
    assert project_metadata.data["kind"] == "file"
    assert project_metadata.data["size_bytes"] == (
        project_root / "core" / "pyproject.toml"
    ).stat().st_size
    assert project_text.success is True
    assert project_text.data is not None
    assert project_text.data["relative_path"] == "core/pyproject.toml"
    assert project_text.data["content_trust"] == "untrusted_data"
    assert project_text.data["instruction_authority"] == "none"
    assert project_text.data["content"].splitlines()[0] == "[build-system]"
    assert conversation.chat_profile.name == "chat_default"
    assert conversation.chat_profile.provider == "deepseek"
    assert conversation.personality_instructions == JARVIS_PERSONALITY_INSTRUCTIONS
    assert conversation.capability_constraints == CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS
    assert isinstance(conversation.memory_store, SQLiteMemoryStore)
    assert conversation.memory_store.database_path == data_dir / "memory.db"
    router = conversation.memory_interaction.router
    assert isinstance(router, SemanticMemoryIntentRouter)
    assert isinstance(router.client, DeepSeekStructuredClient)
    assert router.client is not runtime.brain.client
    assert router.client.settings is runtime.chat_client.settings
    assert runtime.brain.client.settings is runtime.chat_client.settings
    assert conversation.memory_interaction.router_profile.name == "structured_router"
    assert conversation.memory_interaction.router_profile.provider == "deepseek"


def test_production_conversation_can_select_packycode_without_moving_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "disabled")
    monkeypatch.setenv("JARVIS_CHAT_PROFILE", "reasoning_strong")
    monkeypatch.setenv("JARVIS_PACKYCODE_API_KEY", "packycode-key")
    monkeypatch.setenv(
        "JARVIS_PACKYCODE_BASE_URL",
        "https://codex-api.packycode.com/v1",
    )
    monkeypatch.setenv("JARVIS_PROFILE_REASONING_STRONG_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("JARVIS_PROFILE_REASONING_STRONG_EFFORT", "low")
    monkeypatch.chdir(tmp_path)

    conversation = build_conversation(data_dir=tmp_path / "jarvis-data")

    assert isinstance(conversation, LLMConversation)
    runtime = conversation.agent_runtime
    assert isinstance(runtime, AgentRuntime)
    assert isinstance(runtime.chat_client, PackyCodeResponsesClient)
    assert runtime.chat_client.settings.api_key == "packycode-key"
    assert runtime.chat_client.model == "gpt-5.6-sol"
    assert runtime.chat_client.reasoning_effort == "low"
    assert isinstance(runtime.brain, StructuredAgentDecisionModel)
    assert isinstance(runtime.brain.client, DeepSeekStructuredClient)
    assert runtime.brain_profile.name == "agent_brain"
    assert runtime.brain_profile.provider == "deepseek"
    assert runtime.chat_profile.name == "reasoning_strong"
    assert conversation.chat_profile.name == "reasoning_strong"
    assert conversation.chat_profile.provider == "packycode"
    router = conversation.memory_interaction.router
    assert isinstance(router, SemanticMemoryIntentRouter)
    assert isinstance(router.client, DeepSeekStructuredClient)
    assert router.client.settings.api_key == "deepseek-key"
    assert router.client.settings.model == "deepseek-v4-flash"
    assert router.client is not runtime.brain.client
    assert runtime.brain.client.settings.api_key == "deepseek-key"
    assert runtime.brain.client.settings.model == "deepseek-v4-flash"
    assert conversation.memory_interaction.router_profile.name == "structured_router"
    assert conversation.memory_interaction.router_profile.provider == "deepseek"
