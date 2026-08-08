import asyncio
from collections.abc import AsyncIterator
from typing import Protocol


class Conversation(Protocol):
    def stream_reply(self, text: str) -> AsyncIterator[str]: ...


class FakeConversation:
    def __init__(self, *, chunk_delay: float = 0.08) -> None:
        self.chunk_delay = chunk_delay

    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        for chunk in ("晚上好。", "JARVIS Core ", "已上线。"):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield chunk
