from pathlib import Path

import pytest

from jarvis_core.__main__ import build_conversation
from jarvis_core.conversation import LLMConversation
from jarvis_core.llm.deepseek import DeepSeekClient, DeepSeekStructuredClient
from jarvis_core.llm.packycode import PackyCodeResponsesClient
from jarvis_core.memory_router import SemanticMemoryIntentRouter
from jarvis_core.memory_store import SQLiteMemoryStore
from jarvis_core.personality import JARVIS_PERSONALITY_INSTRUCTIONS
from jarvis_core.runtime_capabilities import CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS


def test_production_conversation_uses_deepseek_without_cwd_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "disabled")
    monkeypatch.setenv("JARVIS_CHAT_PROFILE", "chat_default")
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "jarvis-data"

    conversation = build_conversation(data_dir=data_dir)

    assert isinstance(conversation, LLMConversation)
    assert isinstance(conversation.client, DeepSeekClient)
    assert conversation.client.settings.api_key == "test-key"
    assert conversation.client.settings.base_url == "https://api.deepseek.com"
    assert conversation.client.settings.model == "deepseek-v4-flash"
    assert conversation.client.settings.thinking_mode == "disabled"
    assert conversation.chat_profile.name == "chat_default"
    assert conversation.chat_profile.provider == "deepseek"
    assert conversation.personality_instructions == JARVIS_PERSONALITY_INSTRUCTIONS
    assert conversation.capability_constraints == CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS
    assert isinstance(conversation.memory_store, SQLiteMemoryStore)
    assert conversation.memory_store.database_path == data_dir / "memory.db"
    router = conversation.memory_interaction.router
    assert isinstance(router, SemanticMemoryIntentRouter)
    assert isinstance(router.client, DeepSeekStructuredClient)
    assert router.client.settings is conversation.client.settings
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
    assert isinstance(conversation.client, PackyCodeResponsesClient)
    assert conversation.client.settings.api_key == "packycode-key"
    assert conversation.client.model == "gpt-5.6-sol"
    assert conversation.client.reasoning_effort == "low"
    assert conversation.chat_profile.name == "reasoning_strong"
    assert conversation.chat_profile.provider == "packycode"
    router = conversation.memory_interaction.router
    assert isinstance(router, SemanticMemoryIntentRouter)
    assert isinstance(router.client, DeepSeekStructuredClient)
    assert router.client.settings.api_key == "deepseek-key"
    assert router.client.settings.model == "deepseek-v4-flash"
    assert conversation.memory_interaction.router_profile.name == "structured_router"
    assert conversation.memory_interaction.router_profile.provider == "deepseek"
