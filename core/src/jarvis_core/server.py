import asyncio
import json
import logging
from typing import TextIO

from websockets.asyncio.server import Server, ServerConnection, serve

from jarvis_core.agent.contracts import AgentRuntimeError
from jarvis_core.conversation import Conversation
from jarvis_core.llm.client import LLMError
from jarvis_core.memory_store import MemoryStoreError
from jarvis_core.protocol import ProtocolError, build_server_message, parse_client_message
from jarvis_core.state import JarvisState, JarvisStateMachine
from jarvis_core.telemetry import (
    FailurePhase,
    RequestTelemetry,
    bind_request_telemetry,
    reset_request_telemetry,
)

logger = logging.getLogger(__name__)


def emit_process_ready(port: int, stream: TextIO) -> None:
    message = {"type": "process.ready", "port": port, "protocolVersion": 1}
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


class JarvisCoreServer:
    def __init__(self, *, auth_token: str, conversation: Conversation) -> None:
        self.auth_token = auth_token
        self.conversation = conversation
        self.host = "127.0.0.1"
        self.requested_port = 0
        self.port = 0
        self._server: Server | None = None
        self._state = JarvisStateMachine()
        self._chat_lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state.state.value

    async def start(self) -> None:
        self._server = await serve(self._handle_connection, self.host, self.requested_port)
        socket = self._server.sockets[0]
        self.port = int(socket.getsockname()[1])

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        try:
            raw = await websocket.recv()
            if not isinstance(raw, str):
                raise ProtocolError("text messages only")
            message = parse_client_message(raw)
        except ProtocolError:
            await websocket.close(code=4001, reason="authentication required")
            return

        if message.message_type != "auth":
            await websocket.close(code=4001, reason="authentication required")
            return

        if message.payload.get("token") != self.auth_token:
            await websocket.close(code=4003, reason="authentication failed")
            return

        await websocket.send(
            build_server_message("core.ready", {"state": "IDLE"})
        )

        async for raw in websocket:
            if not isinstance(raw, str):
                await websocket.send(
                    build_server_message(
                        "error",
                        {"code": "invalid_message", "message": "text messages only", "retryable": False},
                    )
                )
                continue
            try:
                client_message = parse_client_message(raw)
            except ProtocolError as exc:
                await websocket.send(
                    build_server_message(
                        "error",
                        {"code": "invalid_message", "message": str(exc), "retryable": False},
                        request_id=exc.request_id,
                    )
                )
                continue

            if client_message.message_type != "chat.send":
                await websocket.send(
                    build_server_message(
                        "error",
                        {
                            "code": "unsupported_message",
                            "message": f"unsupported message type: {client_message.message_type}",
                            "retryable": False,
                        },
                    )
                )
                continue

            await self._handle_chat(websocket, client_message.request_id, client_message.payload)

    async def _handle_chat(
        self,
        websocket: ServerConnection,
        request_id: str | None,
        payload: dict[str, object],
    ) -> None:
        assert request_id is not None
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            await websocket.send(
                build_server_message(
                    "error",
                    {"code": "empty_message", "message": "message text is required", "retryable": False},
                    request_id=request_id,
                )
            )
            return

        if self._chat_lock.locked():
            await websocket.send(
                build_server_message(
                    "error",
                    {
                        "code": "busy",
                        "message": "another request is already in progress",
                        "retryable": True,
                    },
                    request_id=request_id,
                )
            )
            return

        async with self._chat_lock:
            await self._run_chat(websocket, request_id, text)

    async def _run_chat(
        self,
        websocket: ServerConnection,
        request_id: str,
        text: str,
    ) -> None:
        telemetry = RequestTelemetry(request_id)
        telemetry_token = bind_request_telemetry(telemetry)
        request_status = "error"

        try:
            await self._send_state(websocket, JarvisState.THINKING, request_id)

            chunks: list[str] = []
            async for chunk in self.conversation.stream_reply(text):
                if not chunk:
                    continue
                if not chunks:
                    await self._send_state(
                        websocket,
                        JarvisState.RESPONDING,
                        request_id,
                    )
                chunks.append(chunk)
                await websocket.send(
                    build_server_message(
                        "chat.delta",
                        {"text": chunk},
                        request_id=request_id,
                    )
                )
                if len(chunks) == 1:
                    telemetry.record_first_delta()

            complete_text = "".join(chunks)
            await websocket.send(
                build_server_message(
                    "chat.completed",
                    {"text": complete_text},
                    request_id=request_id,
                )
            )
            request_status = "success"
        except MemoryStoreError as exc:
            logger.error(
                "Memory operation failed request_id=%s operation=%s "
                "code=memory_unavailable error_type=%s",
                request_id,
                exc.operation,
                exc.error_type,
            )
            await self._send_best_effort(
                websocket,
                build_server_message(
                    "error",
                    {
                        "code": "memory_unavailable",
                        "message": (
                            "本地长期记忆暂时不可用，请重启 JARVIS 后重试。"
                        ),
                        "retryable": True,
                    },
                    request_id=request_id,
                ),
            )
        except LLMError as exc:
            logger.error(
                "LLM reply failed request_id=%s provider=%s code=%s status=%s error_type=%s",
                request_id,
                exc.provider,
                exc.code,
                exc.status_code,
                exc.error_type,
            )
            await self._send_best_effort(
                websocket,
                build_server_message(
                    "error",
                    {
                        "code": exc.code,
                        "message": exc.user_message,
                        "retryable": exc.retryable,
                    },
                    request_id=request_id,
                ),
            )
        except AgentRuntimeError as exc:
            telemetry.mark_failure(FailurePhase.AGENT_BRAIN)
            logger.error(
                "Agent runtime failed request_id=%s stage=%s code=%s error_type=%s",
                request_id,
                exc.stage,
                exc.code,
                exc.error_type,
            )
            await self._send_best_effort(
                websocket,
                build_server_message(
                    "error",
                    {
                        "code": "agent_runtime_unavailable",
                        "message": (
                            "JARVIS 的能力决策暂时不可用，本次没有执行任何工具。"
                        ),
                        "retryable": True,
                    },
                    request_id=request_id,
                ),
            )
        except Exception:
            logger.exception("reply failed for request %s", request_id)
            await self._send_best_effort(
                websocket,
                build_server_message(
                    "error",
                    {"code": "reply_failed", "message": "reply failed", "retryable": True},
                    request_id=request_id,
                ),
            )
        finally:
            try:
                if self._state.state is not JarvisState.IDLE:
                    self._state.transition(JarvisState.IDLE)
                    await self._send_best_effort(
                        websocket,
                        build_server_message(
                            "state.changed",
                            {"state": JarvisState.IDLE.value},
                            request_id=request_id,
                        ),
                    )
            finally:
                try:
                    telemetry.finish(status=request_status)
                finally:
                    reset_request_telemetry(telemetry_token)

    async def _send_best_effort(
        self,
        websocket: ServerConnection,
        message: str,
    ) -> None:
        try:
            await websocket.send(message)
        except Exception:
            logger.debug("request event could not be delivered", exc_info=True)

    async def _send_state(
        self,
        websocket: ServerConnection,
        state: JarvisState,
        request_id: str,
    ) -> None:
        self._state.transition(state)
        await websocket.send(
            build_server_message(
                "state.changed",
                {"state": state.value},
                request_id=request_id,
            )
        )
