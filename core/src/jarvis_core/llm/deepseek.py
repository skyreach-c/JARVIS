from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from jarvis_core.llm.client import ChatMessage, LLMError
from jarvis_core.llm.config import DeepSeekSettings

STRUCTURED_REQUEST_TIMEOUT_SECONDS = 8.0


class DeepSeekClient:
    def __init__(
        self,
        settings: DeepSeekSettings,
        *,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        self.settings = settings
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
                    "DeepSeek API Key 未配置，请在项目根目录 .env 中填写 "
                    "DEEPSEEK_API_KEY。"
                ),
                retryable=False,
                provider="deepseek",
                error_type="MissingApiKey",
            )

        emitted_content = False
        try:
            if self._client is None:
                self._client = self.client_factory(
                    api_key=self.settings.api_key,
                    base_url=self.settings.base_url,
                )

            sdk_messages = [dict(message) for message in messages]
            stream = await self._client.chat.completions.create(
                model=self.settings.model,
                messages=sdk_messages,
                stream=True,
                extra_body={"thinking": {"type": self.settings.thinking_mode}},
            )
            async for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                if not isinstance(content, str) or not content:
                    continue
                emitted_content = True
                yield content
        except LLMError:
            raise
        except openai.APITimeoutError as exc:
            raise _llm_error(
                exc,
                code="llm_timeout",
                user_message="DeepSeek 请求超时，请稍后重试。",
                retryable=True,
            ) from None
        except openai.APIConnectionError as exc:
            raise _llm_error(
                exc,
                code="llm_unavailable",
                user_message="DeepSeek 服务暂时不可用，请稍后重试。",
                retryable=True,
            ) from None
        except openai.APIStatusError as exc:
            raise _status_error(exc) from None
        except openai.APIError as exc:
            raise _llm_error(
                exc,
                code="llm_provider_error",
                user_message="DeepSeek 请求失败，请稍后重试。",
                retryable=False,
            ) from None

        if not emitted_content:
            raise LLMError(
                code="llm_empty_response",
                user_message="DeepSeek 未返回可显示的回复，请重试。",
                retryable=True,
                provider="deepseek",
                error_type="EmptyResponse",
            )


class DeepSeekStructuredClient:
    def __init__(
        self,
        settings: DeepSeekSettings,
        *,
        client_factory: Callable[..., Any] = AsyncOpenAI,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory
        self._client: Any | None = None

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
    ) -> str:
        if not self.settings.api_key:
            raise LLMError(
                code="llm_not_configured",
                user_message=(
                    "DeepSeek API Key 未配置，请在项目根目录 .env 中填写"
                    "DEEPSEEK_API_KEY。"
                ),
                retryable=False,
                provider="deepseek",
                error_type="MissingApiKey",
            )

        try:
            if self._client is None:
                self._client = self.client_factory(
                    api_key=self.settings.api_key,
                    base_url=self.settings.base_url,
                    timeout=STRUCTURED_REQUEST_TIMEOUT_SECONDS,
                    max_retries=0,
                )

            sdk_messages = [dict(message) for message in messages]
            response = await self._client.chat.completions.create(
                model=self.settings.model,
                messages=sdk_messages,
                stream=False,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                temperature=0,
                max_tokens=max_tokens,
            )
            choices = getattr(response, "choices", None)
            if not choices:
                raise _structured_response_error(
                    code="llm_empty_response",
                    error_type="EmptyStructuredResponse",
                )
            first_choice = choices[0]
            if getattr(first_choice, "finish_reason", None) == "length":
                raise _structured_response_error(
                    code="llm_truncated_response",
                    error_type="TruncatedStructuredResponse",
                )
            message = getattr(first_choice, "message", None)
            content = getattr(message, "content", None)
            if not isinstance(content, str) or not content.strip():
                raise _structured_response_error(
                    code="llm_empty_response",
                    error_type="EmptyStructuredResponse",
                )
            return content
        except LLMError:
            raise
        except openai.APITimeoutError as exc:
            raise _llm_error(
                exc,
                code="llm_timeout",
                user_message="DeepSeek 请求超时，请稍后重试。",
                retryable=True,
            ) from None
        except openai.APIConnectionError as exc:
            raise _llm_error(
                exc,
                code="llm_unavailable",
                user_message="DeepSeek 服务暂时不可用，请稍后重试。",
                retryable=True,
            ) from None
        except openai.APIStatusError as exc:
            raise _status_error(exc) from None
        except openai.APIError as exc:
            raise _llm_error(
                exc,
                code="llm_provider_error",
                user_message="DeepSeek 请求失败，请稍后重试。",
                retryable=False,
            ) from None


def _structured_response_error(*, code: str, error_type: str) -> LLMError:
    return LLMError(
        code=code,
        user_message="Memory Router 未返回可验证的结构化结果。",
        retryable=True,
        provider="deepseek",
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
        provider="deepseek",
        status_code=status_code,
        error_type=type(error).__name__,
    )


def _status_error(error: openai.APIStatusError) -> LLMError:
    status_code = error.status_code
    if status_code in {400, 422}:
        return _llm_error(
            error,
            code="llm_bad_request",
            user_message="DeepSeek 拒绝了当前请求，请检查模型和请求配置。",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 401:
        return _llm_error(
            error,
            code="llm_auth_failed",
            user_message="DeepSeek API Key 无效，请检查项目根目录 .env。",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 402:
        return _llm_error(
            error,
            code="llm_balance_insufficient",
            user_message="DeepSeek API 余额不足，请检查账户余额。",
            retryable=False,
            status_code=status_code,
        )
    if status_code == 429:
        return _llm_error(
            error,
            code="llm_rate_limited",
            user_message="DeepSeek 请求过于频繁，请稍后重试。",
            retryable=True,
            status_code=status_code,
        )
    if 500 <= status_code < 600:
        return _llm_error(
            error,
            code="llm_unavailable",
            user_message="DeepSeek 服务暂时不可用，请稍后重试。",
            retryable=True,
            status_code=status_code,
        )
    return _llm_error(
        error,
        code="llm_provider_error",
        user_message="DeepSeek 请求失败，请稍后重试。",
        retryable=False,
        status_code=status_code,
    )
