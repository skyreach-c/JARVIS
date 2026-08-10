import ast
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

import jarvis_core.llm.deepseek as deepseek_module
from jarvis_core.llm.client import ChatMessage, LLMError, StructuredLLMClient
from jarvis_core.llm.config import DeepSeekSettings
from jarvis_core.llm.deepseek import DeepSeekClient, DeepSeekStructuredClient


class FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[object]:
        for chunk in self.chunks:
            yield chunk


class FakeCompletions:
    def __init__(
        self,
        *,
        chunks: list[object] | None = None,
        response: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = chunks or []
        self.response = response
        self.error = error
        self.request: dict[str, Any] | None = None
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.request = kwargs
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return FakeStream(self.chunks)


class FakeSDKClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


DEFAULT_MESSAGES: tuple[ChatMessage, ...] = (
    {"role": "system", "content": "SYSTEM_SENTINEL"},
    {"role": "user", "content": "你好"},
)


def settings(
    *,
    api_key: str | None = "test-key",
    thinking_mode: str = "disabled",
) -> DeepSeekSettings:
    return DeepSeekSettings(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        thinking_mode=thinking_mode,  # type: ignore[arg-type]
    )


def chunk(*, choices: list[object] | None = None) -> object:
    return SimpleNamespace(choices=choices)


def choice(content: object = None) -> object:
    return SimpleNamespace(delta=SimpleNamespace(content=content))


async def collect(
    client: DeepSeekClient,
    messages: tuple[ChatMessage, ...] = DEFAULT_MESSAGES,
) -> list[str]:
    return [part async for part in client.stream_chat(messages)]


async def test_streams_only_non_empty_text_and_sends_minimal_v4_request() -> None:
    completions = FakeCompletions(
        chunks=[
            chunk(choices=[]),
            chunk(choices=[choice(None)]),
            chunk(choices=[choice("")]),
            chunk(choices=[choice(123)]),
            chunk(choices=[choice("晚上好")]),
            SimpleNamespace(),
            chunk(choices=[choice("。")]),
        ]
    )
    sdk_client = FakeSDKClient(completions)
    factory_options: dict[str, object] = {}

    def client_factory(**kwargs: object) -> FakeSDKClient:
        factory_options.update(kwargs)
        return sdk_client

    client = DeepSeekClient(settings(), client_factory=client_factory)
    original_messages = tuple(dict(message) for message in DEFAULT_MESSAGES)

    parts = await collect(client)

    assert parts == ["晚上好", "。"]
    assert factory_options == {
        "api_key": "test-key",
        "base_url": "https://api.deepseek.com",
    }
    assert completions.request == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "SYSTEM_SENTINEL"},
            {"role": "user", "content": "你好"},
        ],
        "stream": True,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    sdk_messages = completions.request["messages"]
    assert isinstance(sdk_messages, list)
    assert sdk_messages is not DEFAULT_MESSAGES
    assert sdk_messages[0] is not DEFAULT_MESSAGES[0]
    assert DEFAULT_MESSAGES == original_messages


def test_provider_has_no_personality_or_runtime_capability_dependency() -> None:
    source = Path(deepseek_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "jarvis_core.personality" not in imported_modules
    assert "jarvis_core.runtime_capabilities" not in imported_modules
    assert not any(
        module.startswith("jarvis_core.memory") for module in imported_modules
    )
    assert "Identity / Personality" not in source
    assert "Current Runtime Capabilities" not in source


async def test_provider_does_not_retain_messages_between_calls() -> None:
    completions = FakeCompletions(chunks=[chunk(choices=[choice("reply")])])
    sdk_client = FakeSDKClient(completions)
    client = DeepSeekClient(settings(), client_factory=lambda **_: sdk_client)
    first_messages: tuple[ChatMessage, ...] = (
        {"role": "system", "content": "first system"},
        {"role": "user", "content": "first user"},
    )
    second_messages: tuple[ChatMessage, ...] = (
        {"role": "system", "content": "second system"},
        {"role": "user", "content": "second user"},
    )

    await collect(client, first_messages)
    await collect(client, second_messages)

    assert [request["messages"] for request in completions.requests] == [
        [
            {"role": "system", "content": "first system"},
            {"role": "user", "content": "first user"},
        ],
        [
            {"role": "system", "content": "second system"},
            {"role": "user", "content": "second user"},
        ],
    ]


async def test_missing_api_key_is_reported_before_client_creation() -> None:
    factory_called = False

    def factory(**_: object) -> object:
        nonlocal factory_called
        factory_called = True
        raise AssertionError("client factory must not run without a key")

    client = DeepSeekClient(settings(api_key=None), client_factory=factory)

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.code == "llm_not_configured"
    assert raised.value.retryable is False
    assert factory_called is False


async def test_empty_stream_is_a_clear_error() -> None:
    sdk_client = FakeSDKClient(FakeCompletions(chunks=[chunk(choices=[])]))
    client = DeepSeekClient(settings(), client_factory=lambda **_: sdk_client)

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.code == "llm_empty_response"


def structured_response(
    content: object,
    *,
    finish_reason: str = "stop",
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


async def test_structured_client_sends_explicit_non_thinking_json_request() -> None:
    completions = FakeCompletions(response=structured_response('{"action":"chat"}'))
    sdk_client = FakeSDKClient(completions)
    factory_options: dict[str, object] = {}

    def client_factory(**kwargs: object) -> FakeSDKClient:
        factory_options.update(kwargs)
        return sdk_client

    client: StructuredLLMClient = DeepSeekStructuredClient(
        settings(thinking_mode="enabled"),
        client_factory=client_factory,
    )
    original_messages = tuple(dict(message) for message in DEFAULT_MESSAGES)

    result = await client.complete_json(DEFAULT_MESSAGES, max_tokens=1024)

    assert result == '{"action":"chat"}'
    assert factory_options == {
        "api_key": "test-key",
        "base_url": "https://api.deepseek.com",
        "timeout": 8.0,
        "max_retries": 0,
    }
    assert completions.request == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "SYSTEM_SENTINEL"},
            {"role": "user", "content": "你好"},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
        "temperature": 0,
        "max_tokens": 1024,
    }
    assert completions.request["messages"] is not DEFAULT_MESSAGES
    assert completions.request["messages"][0] is not DEFAULT_MESSAGES[0]
    assert DEFAULT_MESSAGES == original_messages


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (SimpleNamespace(choices=[]), "llm_empty_response"),
        (SimpleNamespace(), "llm_empty_response"),
        (structured_response(None), "llm_empty_response"),
        (structured_response(""), "llm_empty_response"),
        (structured_response("   "), "llm_empty_response"),
        (structured_response("{}", finish_reason="length"), "llm_truncated_response"),
    ],
)
async def test_structured_client_fails_closed_on_empty_or_truncated_output(
    response: object,
    expected_code: str,
) -> None:
    sdk_client = FakeSDKClient(FakeCompletions(response=response))
    client = DeepSeekStructuredClient(
        settings(),
        client_factory=lambda **_: sdk_client,
    )

    with pytest.raises(LLMError) as raised:
        await client.complete_json(DEFAULT_MESSAGES, max_tokens=1024)

    assert raised.value.code == expected_code


async def test_structured_client_missing_key_fails_before_sdk_creation() -> None:
    factory_called = False

    def factory(**_: object) -> object:
        nonlocal factory_called
        factory_called = True
        raise AssertionError("factory must not run")

    client = DeepSeekStructuredClient(settings(api_key=None), client_factory=factory)

    with pytest.raises(LLMError) as raised:
        await client.complete_json(DEFAULT_MESSAGES, max_tokens=1024)

    assert raised.value.code == "llm_not_configured"
    assert factory_called is False


def status_error(status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(
        "sanitized-test-error",
        response=response,
        body={"error": "secret-response-body"},
    )


@pytest.mark.parametrize(
    ("sdk_error", "expected_code", "expected_retryable"),
    [
        (openai.APITimeoutError(httpx.Request("POST", "https://api.deepseek.com")), "llm_timeout", True),
        (
            openai.APIConnectionError(
                request=httpx.Request("POST", "https://api.deepseek.com")
            ),
            "llm_unavailable",
            True,
        ),
        (status_error(400), "llm_bad_request", False),
        (status_error(422), "llm_bad_request", False),
        (status_error(401), "llm_auth_failed", False),
        (status_error(402), "llm_balance_insufficient", False),
        (status_error(429), "llm_rate_limited", True),
        (status_error(500), "llm_unavailable", True),
        (status_error(503), "llm_unavailable", True),
        (status_error(418), "llm_provider_error", False),
    ],
)
async def test_sdk_errors_are_mapped(
    sdk_error: Exception,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    sdk_client = FakeSDKClient(FakeCompletions(error=sdk_error))
    client = DeepSeekClient(settings(), client_factory=lambda **_: sdk_client)

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.code == expected_code
    assert raised.value.retryable is expected_retryable
    assert raised.value.status_code == getattr(sdk_error, "status_code", None)
    assert raised.value.provider == "deepseek"
    assert raised.value.error_type == type(sdk_error).__name__
