import ast
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

import jarvis_core.llm.packycode as packycode_module
from jarvis_core.llm.client import ChatMessage, LLMError
from jarvis_core.llm.config import PackyCodeSettings
from jarvis_core.llm.packycode import (
    PACKYCODE_MAX_RETRIES,
    PACKYCODE_REQUEST_TIMEOUT_SECONDS,
    PackyCodeResponsesClient,
)

MESSAGES: tuple[ChatMessage, ...] = (
    {"role": "system", "content": "SYSTEM_SENTINEL"},
    {"role": "user", "content": "USER_SENTINEL"},
)


class FakeStream:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[object]:
        for event in self.events:
            yield event

    async def close(self) -> None:
        self.closed = True


class FakeResponses:
    def __init__(
        self,
        *,
        events: list[object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.stream = FakeStream(events or [])
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeStream:
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.stream


class FakeSDKClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


def event(event_type: str, *, delta: object = None) -> object:
    return SimpleNamespace(type=event_type, delta=delta)


def settings(api_key: str | None = "packycode-key") -> PackyCodeSettings:
    return PackyCodeSettings(
        api_key=api_key,
        base_url="https://codex-api.packycode.com/v1",
    )


async def collect(client: PackyCodeResponsesClient) -> list[str]:
    return [chunk async for chunk in client.stream_chat(MESSAGES)]


async def test_client_is_lazy_and_sends_profile_owned_responses_request() -> None:
    responses = FakeResponses(
        events=[
            event("response.output_text.delta", delta="hello"),
            event("response.completed"),
        ]
    )
    sdk_client = FakeSDKClient(responses)
    factory_calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeSDKClient:
        factory_calls.append(kwargs)
        return sdk_client

    client = PackyCodeResponsesClient(
        settings(),
        model="profile-model",
        reasoning_effort="low",
        client_factory=factory,
    )
    original_messages = tuple(dict(message) for message in MESSAGES)

    assert factory_calls == []
    assert await collect(client) == ["hello"]
    assert factory_calls == [
        {
            "api_key": "packycode-key",
            "base_url": "https://codex-api.packycode.com/v1",
            "timeout": PACKYCODE_REQUEST_TIMEOUT_SECONDS,
            "max_retries": PACKYCODE_MAX_RETRIES,
        }
    ]
    assert responses.requests == [
        {
            "model": "profile-model",
            "input": [
                {"role": "system", "content": "SYSTEM_SENTINEL"},
                {"role": "user", "content": "USER_SENTINEL"},
            ],
            "stream": True,
            "store": False,
            "reasoning": {"effort": "low"},
        }
    ]
    assert responses.requests[0]["input"] is not MESSAGES
    assert responses.requests[0]["input"][0] is not MESSAGES[0]
    assert MESSAGES == original_messages
    assert responses.stream.closed is True


async def test_reasoning_is_omitted_when_profile_effort_is_none() -> None:
    responses = FakeResponses(
        events=[event("response.output_text.delta", delta="ok"), event("response.completed")]
    )
    client = PackyCodeResponsesClient(
        settings(),
        model="profile-model",
        reasoning_effort=None,
        client_factory=lambda **_: FakeSDKClient(responses),
    )

    assert await collect(client) == ["ok"]
    assert "reasoning" not in responses.requests[0]


async def test_output_and_refusal_deltas_are_both_visible() -> None:
    responses = FakeResponses(
        events=[
            event("response.reasoning.delta", delta="hidden"),
            event("response.output_text.delta", delta="visible"),
            event("response.refusal.delta", delta=" refusal"),
            event("response.output_text.delta", delta=""),
            event("response.refusal.delta", delta=None),
            event("response.refusal.done", delta="must not repeat"),
            event("response.completed"),
        ]
    )
    client = PackyCodeResponsesClient(
        settings(),
        model="profile-model",
        reasoning_effort="low",
        client_factory=lambda **_: FakeSDKClient(responses),
    )

    assert await collect(client) == ["visible", " refusal"]


@pytest.mark.parametrize(
    ("events", "expected_code"),
    [
        ([event("response.output_text.delta", delta="partial")], "llm_truncated_response"),
        ([event("response.failed")], "llm_provider_error"),
        ([event("response.incomplete")], "llm_truncated_response"),
        ([event("error")], "llm_provider_error"),
        ([event("response.completed")], "llm_empty_response"),
    ],
)
async def test_stream_requires_completed_and_visible_text(
    events: list[object],
    expected_code: str,
) -> None:
    responses = FakeResponses(events=events)
    client = PackyCodeResponsesClient(
        settings(),
        model="profile-model",
        reasoning_effort="low",
        client_factory=lambda **_: FakeSDKClient(responses),
    )

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.code == expected_code
    assert raised.value.provider == "packycode"
    assert responses.stream.closed is True


async def test_missing_key_fails_on_iteration_without_sdk_creation() -> None:
    factory_calls = 0

    def factory(**_: object) -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("SDK must not be created without a key")

    client = PackyCodeResponsesClient(
        settings(api_key=None),
        model="profile-model",
        reasoning_effort="low",
        client_factory=factory,
    )

    assert factory_calls == 0
    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.code == "llm_not_configured"
    assert factory_calls == 0


async def test_consumer_close_closes_provider_stream() -> None:
    responses = FakeResponses(
        events=[
            event("response.output_text.delta", delta="partial"),
            event("response.output_text.delta", delta="unread"),
            event("response.completed"),
        ]
    )
    client = PackyCodeResponsesClient(
        settings(),
        model="profile-model",
        reasoning_effort="low",
        client_factory=lambda **_: FakeSDKClient(responses),
    )
    reply = client.stream_chat(MESSAGES)

    assert await anext(reply) == "partial"
    await reply.aclose()

    assert responses.stream.closed is True


def status_error(status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://codex-api.packycode.com/v1/responses")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(
        "sanitized-test-error",
        response=response,
        body={"error": "private-provider-body"},
    )


@pytest.mark.parametrize(
    ("sdk_error", "expected_code", "expected_retryable"),
    [
        (
            openai.APITimeoutError(
                httpx.Request("POST", "https://codex-api.packycode.com/v1/responses")
            ),
            "llm_timeout",
            True,
        ),
        (
            openai.APIConnectionError(
                request=httpx.Request(
                    "POST", "https://codex-api.packycode.com/v1/responses"
                )
            ),
            "llm_unavailable",
            True,
        ),
        (status_error(400), "llm_bad_request", False),
        (status_error(404), "llm_bad_request", False),
        (status_error(422), "llm_bad_request", False),
        (status_error(401), "llm_auth_failed", False),
        (status_error(403), "llm_auth_failed", False),
        (status_error(402), "llm_balance_insufficient", False),
        (status_error(429), "llm_rate_limited", True),
        (status_error(500), "llm_unavailable", True),
        (status_error(418), "llm_provider_error", False),
    ],
)
async def test_sdk_errors_are_mapped_without_exposing_provider_body(
    sdk_error: Exception,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    responses = FakeResponses(error=sdk_error)
    client = PackyCodeResponsesClient(
        settings(),
        model="profile-model",
        reasoning_effort="low",
        client_factory=lambda **_: FakeSDKClient(responses),
    )

    with pytest.raises(LLMError) as raised:
        await collect(client)

    assert raised.value.code == expected_code
    assert raised.value.retryable is expected_retryable
    assert raised.value.provider == "packycode"
    assert "private-provider-body" not in str(raised.value)


def test_provider_has_no_core_personality_memory_or_hardcoded_profile_policy() -> None:
    source = Path(packycode_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "jarvis_core.personality" not in imported_modules
    assert "jarvis_core.runtime_capabilities" not in imported_modules
    assert not any(module.startswith("jarvis_core.memory") for module in imported_modules)
    assert "gpt-5.6-sol" not in source
    assert 'reasoning_effort="low"' not in source
