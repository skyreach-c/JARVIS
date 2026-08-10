import logging
from collections.abc import AsyncIterator, Callable, Sequence
from inspect import isawaitable
from typing import Any

import openai
from openai import AsyncOpenAI

from jarvis_core.llm.client import ChatMessage, LLMError
from jarvis_core.llm.config import PackyCodeSettings
from jarvis_core.llm.profiles import ReasoningEffort

PACKYCODE_REQUEST_TIMEOUT_SECONDS = 60.0
PACKYCODE_MAX_RETRIES = 0
logger = logging.getLogger(__name__)
_VISIBLE_DELTA_EVENTS = frozenset(
    {"response.output_text.delta", "response.refusal.delta"}
)


class PackyCodeResponsesClient:
    def __init__(
        self,
        settings: PackyCodeSettings,
        *,
        model: str,
        reasoning_effort: ReasoningEffort | None,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        self.settings = settings
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.client_factory = client_factory
        self._client: Any | None = None

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        if not self.settings.api_key:
            raise LLMError(
                code="llm_not_configured",
                user_message=(
                    "PackyCode API Key 未配置，请在项目根目录 .env 中填写 "
                    "JARVIS_PACKYCODE_API_KEY。"
                ),
                retryable=False,
                provider="packycode",
                error_type="MissingApiKey",
            )

        stream: Any | None = None
        emitted_content = False
        completed = False
        try:
            if self._client is None:
                self._client = self.client_factory(
                    api_key=self.settings.api_key,
                    base_url=self.settings.base_url,
                    timeout=PACKYCODE_REQUEST_TIMEOUT_SECONDS,
                    max_retries=PACKYCODE_MAX_RETRIES,
                )

            request: dict[str, Any] = {
                "model": self.model,
                "input": [dict(message) for message in messages],
                "stream": True,
                "store": False,
            }
            if self.reasoning_effort is not None:
                request["reasoning"] = {"effort": self.reasoning_effort}

            stream = await self._client.responses.create(**request)
            async for response_event in stream:
                event_type = getattr(response_event, "type", None)
                if event_type in _VISIBLE_DELTA_EVENTS:
                    delta = getattr(response_event, "delta", None)
                    if isinstance(delta, str) and delta:
                        emitted_content = True
                        yield delta
                    continue
                if event_type == "response.completed":
                    completed = True
                    break
                if event_type == "response.incomplete":
                    raise _stream_error(
                        code="llm_truncated_response",
                        error_type="IncompleteResponse",
                    )
                if event_type in {"response.failed", "error"}:
                    raise _stream_error(
                        code="llm_provider_error",
                        error_type="FailedResponse",
                    )
        except LLMError:
            raise
        except openai.APITimeoutError as exc:
            raise _llm_error(
                exc,
                code="llm_timeout",
                user_message="PackyCode 请求超时，请稍后重试。",
                retryable=True,
            ) from None
        except openai.APIConnectionError as exc:
            raise _llm_error(
                exc,
                code="llm_unavailable",
                user_message="PackyCode 服务暂时不可用，请稍后重试。",
                retryable=True,
            ) from None
        except openai.APIStatusError as exc:
            raise _status_error(exc) from None
        except openai.APIError as exc:
            raise _llm_error(
                exc,
                code="llm_provider_error",
                user_message="PackyCode 请求失败，请稍后重试。",
                retryable=False,
            ) from None
        finally:
            if stream is not None:
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close_result = close()
                        if isawaitable(close_result):
                            await close_result
                    except Exception:  # noqa: BLE001 - cleanup is best-effort
                        logger.debug("PackyCode response stream close failed")

        if not completed:
            raise _stream_error(
                code="llm_truncated_response",
                error_type="MissingCompletedEvent",
            )
        if not emitted_content:
            raise _stream_error(
                code="llm_empty_response",
                error_type="EmptyResponse",
            )


def _stream_error(*, code: str, error_type: str) -> LLMError:
    return LLMError(
        code=code,
        user_message="PackyCode 未返回完整的可显示回复，请重试。",
        retryable=True,
        provider="packycode",
        error_type=error_type,
    )


def _llm_error(
    error: Exception,
    *,
    code: str,
    user_message: str,
    retryable: bool,
    status_code: int | None = None,
) -> LLMError:
    return LLMError(
        code=code,
        user_message=user_message,
        retryable=retryable,
        provider="packycode",
        status_code=status_code,
        error_type=type(error).__name__,
    )


def _status_error(error: openai.APIStatusError) -> LLMError:
    status_code = error.status_code
    if status_code in {400, 404, 422}:
        return _llm_error(
            error,
            code="llm_bad_request",
            user_message="PackyCode 拒绝了当前请求，请检查模型和请求配置。",
            retryable=False,
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return _llm_error(
            error,
            code="llm_auth_failed",
            user_message="PackyCode API Key 无效或无权使用当前模型。",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 402:
        return _llm_error(
            error,
            code="llm_balance_insufficient",
            user_message="PackyCode API 余额不足，请检查账户余额。",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 429:
        return _llm_error(
            error,
            code="llm_rate_limited",
            user_message="PackyCode 请求过于频繁，请稍后重试。",
            retryable=True,
            status_code=status_code,
        )
    if 500 <= status_code < 600:
        return _llm_error(
            error,
            code="llm_unavailable",
            user_message="PackyCode 服务暂时不可用，请稍后重试。",
            retryable=True,
            status_code=status_code,
        )
    return _llm_error(
        error,
        code="llm_provider_error",
        user_message="PackyCode 请求失败，请稍后重试。",
        retryable=False,
        status_code=status_code,
    )
