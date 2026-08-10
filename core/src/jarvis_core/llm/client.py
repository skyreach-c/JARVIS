from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol, TypedDict


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMClient(Protocol):
    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]: ...


class StructuredLLMClient(Protocol):
    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
    ) -> str: ...


class LLMError(Exception):
    def __init__(
        self,
        *,
        code: str,
        user_message: str,
        retryable: bool,
        provider: str,
        status_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.provider = provider
        self.status_code = status_code
        self.error_type = error_type
