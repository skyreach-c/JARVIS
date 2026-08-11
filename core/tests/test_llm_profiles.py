from jarvis_core.llm.config import DeepSeekSettings, PackyCodeSettings
from jarvis_core.llm.deepseek import DeepSeekClient, DeepSeekStructuredClient
from jarvis_core.llm.packycode import PackyCodeResponsesClient
from jarvis_core.llm.profiles import (
    build_model_profiles,
    create_chat_client,
    create_structured_client,
)


def deepseek_settings() -> DeepSeekSettings:
    return DeepSeekSettings(
        api_key="deepseek-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        thinking_mode="disabled",
    )


def packycode_settings() -> PackyCodeSettings:
    return PackyCodeSettings(
        api_key="packycode-key",
        base_url="https://codex-api.packycode.com/v1",
    )


def test_profile_catalog_distinguishes_system_and_chat_profiles() -> None:
    profiles = build_model_profiles(
        deepseek_model="deepseek-v4-flash",
        reasoning_strong_model="gpt-5.6-sol",
        reasoning_strong_effort="low",
    )

    assert tuple(profiles) == (
        "chat_default",
        "reasoning_strong",
        "structured_router",
        "agent_brain",
    )
    assert profiles["chat_default"].reasoning_effort is None
    assert profiles["reasoning_strong"].provider == "packycode"
    assert profiles["reasoning_strong"].model == "gpt-5.6-sol"
    assert profiles["reasoning_strong"].reasoning_effort == "low"
    assert profiles["structured_router"].provider == "deepseek"
    assert profiles["agent_brain"].provider == "deepseek"
    assert profiles["agent_brain"].model == "deepseek-v4-flash"
    assert profiles["agent_brain"].reasoning_effort is None


def test_chat_factories_create_the_selected_provider_without_moving_router() -> None:
    profiles = build_model_profiles(
        deepseek_model="deepseek-v4-flash",
        reasoning_strong_model="gpt-5.6-sol",
        reasoning_strong_effort="low",
    )
    deepseek = deepseek_settings()
    packycode = packycode_settings()

    default_client = create_chat_client(
        profiles["chat_default"],
        deepseek_settings=deepseek,
        packycode_settings=packycode,
    )
    strong_client = create_chat_client(
        profiles["reasoning_strong"],
        deepseek_settings=deepseek,
        packycode_settings=packycode,
    )
    router_client = create_structured_client(
        profiles["structured_router"],
        deepseek_settings=deepseek,
    )
    brain_client = create_structured_client(
        profiles["agent_brain"],
        deepseek_settings=deepseek,
    )

    assert isinstance(default_client, DeepSeekClient)
    assert isinstance(strong_client, PackyCodeResponsesClient)
    assert strong_client.model == "gpt-5.6-sol"
    assert strong_client.reasoning_effort == "low"
    assert isinstance(router_client, DeepSeekStructuredClient)
    assert isinstance(brain_client, DeepSeekStructuredClient)
    assert brain_client is not router_client


def test_chat_profile_switch_does_not_change_system_profiles() -> None:
    profiles = build_model_profiles(
        deepseek_model="deepseek-v4-flash",
        reasoning_strong_model="gpt-5.6-sol",
        reasoning_strong_effort="low",
    )

    for selected_chat_profile in ("chat_default", "reasoning_strong"):
        assert profiles[selected_chat_profile].name == selected_chat_profile
        assert profiles["structured_router"].provider == "deepseek"
        assert profiles["agent_brain"].provider == "deepseek"
