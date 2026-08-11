import asyncio
import io
import json
import logging
import os
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from jarvis_core.agent.contracts import AgentRuntimeError
from jarvis_core.conversation import FakeConversation, LLMConversation
from jarvis_core.llm.client import LLMError
from jarvis_core.memory_router import MemoryIntent
from jarvis_core.memory_store import MemoryStoreError, SQLiteMemoryStore
from jarvis_core.server import JarvisCoreServer, emit_process_ready
from jarvis_core.telemetry import current_request_telemetry


class FailingConversation:
    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        if False:
            yield ""
        raise RuntimeError("fake provider exploded")


class FailingAgentRuntimeConversation:
    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        if False:
            yield ""
        raise AgentRuntimeError(
            stage="provider",
            code="agent_brain_unavailable",
            error_type="ProviderError",
        )


class BlockingConversation:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        self.started.set()
        await self.release.wait()
        yield "done"


class LLMFailingConversation:
    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        if False:
            yield ""
        raise LLMError(
            code="llm_bad_request",
            user_message="DeepSeek 拒绝了当前请求，请检查模型和请求配置。",
            retryable=False,
            provider="deepseek",
            status_code=400,
            error_type="BadRequestError",
        )


class MemoryFailingConversation:
    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        if False:
            yield ""
        raise MemoryStoreError(
            operation="list_memories",
            error_type="OperationalError",
        )


class PartialMemoryFailingConversation:
    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        yield "visible partial"
        raise MemoryStoreError(
            operation="list_memories",
            error_type="OperationalError",
        )


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    @property
    def events(self) -> list[dict[str, object]]:
        return [json.loads(message) for message in self.sent]


class SingleChunkLLMClient:
    async def stream_chat(self, messages):  # type: ignore[no-untyped-def]
        del messages
        yield "visible answer"


class PassthroughAgentRuntime:
    def __init__(self, client):  # type: ignore[no-untyped-def]
        self.client = client

    async def stream_response(
        self,
        messages,
        *,
        current_user_message,
        request_id,
    ):  # type: ignore[no-untyped-def]
        del current_user_message, request_id
        async for chunk in self.client.stream_chat(messages):
            yield chunk


class ChatMemoryRouter:
    async def route(self, request):  # type: ignore[no-untyped-def]
        del request
        return MemoryIntent.model_validate(
            {
                "action": "chat",
                "content": None,
                "memory_ids": [],
                "evidence": [],
            }
        )


class ClearAllMemoryRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def route(self, request):  # type: ignore[no-untyped-def]
        del request
        self.calls += 1
        return MemoryIntent.model_validate({"action": "clear_all"})


def make_llm_conversation(tmp_path: Path) -> LLMConversation:
    return LLMConversation(
        PassthroughAgentRuntime(SingleChunkLLMClient()),
        personality_instructions="personality",
        capability_constraints="capabilities",
        memory_store=SQLiteMemoryStore(tmp_path / "memory.db"),
        memory_router=ChatMemoryRouter(),
    )


def perf_summaries(
    caplog: pytest.LogCaptureFixture,
) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage().removeprefix("PERF "))
        for record in caplog.records
        if record.name == "jarvis_core.perf"
        and record.getMessage().startswith("PERF ")
    ]


async def test_server_uses_os_assigned_loopback_port() -> None:
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )

    await server.start()
    try:
        assert server.host == "127.0.0.1"
        assert server.requested_port == 0
        assert server.port > 0
    finally:
        await server.stop()


def test_process_readiness_is_one_machine_readable_stdout_line() -> None:
    stdout = io.StringIO()

    emit_process_ready(54321, stdout)

    assert stdout.getvalue() == (
        '{"type":"process.ready","port":54321,"protocolVersion":1}\n'
    )


async def test_core_ready_is_sent_only_after_successful_authentication() -> None:
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )

            ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.2))

            assert ready == {
                "version": 1,
                "type": "core.ready",
                "payload": {"state": "IDLE"},
            }
    finally:
        await server.stop()


async def test_chat_send_before_authentication_is_rejected_as_auth_required() -> None:
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": "request-before-auth",
                        "payload": {"text": "hello"},
                    }
                )
            )

            with pytest.raises(ConnectionClosedError) as closed:
                await websocket.recv()

            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 4001
    finally:
        await server.stop()


async def test_wrong_token_is_rejected_without_core_ready() -> None:
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "wrong-token"},
                    }
                )
            )

            with pytest.raises(ConnectionClosedError) as closed:
                await websocket.recv()

            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 4003
    finally:
        await server.stop()


async def test_chat_send_emits_ordered_correlated_heartbeat_events() -> None:
    request_id = "heartbeat-request"
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await asyncio.wait_for(websocket.recv(), timeout=0.5)

            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": request_id,
                        "payload": {"text": "你好"},
                    }
                )
            )

            events: list[dict[str, object]] = []
            while True:
                event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                events.append(event)
                if event["type"] == "state.changed" and event["payload"] == {
                    "state": "IDLE"
                }:
                    break

            event_types = [event["type"] for event in events]
            responding_index = next(
                index
                for index, event in enumerate(events)
                if event["type"] == "state.changed"
                and event["payload"] == {"state": "RESPONDING"}
            )
            delta_indexes = [
                index for index, event in enumerate(events) if event["type"] == "chat.delta"
            ]

            assert events[0]["payload"] == {"state": "THINKING"}
            assert responding_index == 1
            assert delta_indexes
            assert min(delta_indexes) > responding_index
            assert event_types[-2:] == ["chat.completed", "state.changed"]
            assert all(event["requestId"] == request_id for event in events)

            chunks = [
                event["payload"]["text"]
                for event in events
                if event["type"] == "chat.delta"
            ]
            completed = next(event for event in events if event["type"] == "chat.completed")
            assert "".join(chunks) == "晚上好。JARVIS Core 已上吗。"
            assert completed["payload"] == {"text": "".join(chunks)}
    finally:
        await server.stop()


async def test_empty_chat_error_preserves_request_id_without_changing_state() -> None:
    request_id = "empty-request"
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await asyncio.wait_for(websocket.recv(), timeout=0.5)
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": request_id,
                        "payload": {"text": "   "},
                    }
                )
            )

            event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.5))

            assert event["type"] == "error"
            assert event["requestId"] == request_id
            assert event["payload"]["code"] == "empty_message"
            assert server.state == "IDLE"
    finally:
        await server.stop()


async def test_concurrent_chat_is_rejected_with_its_original_request_id() -> None:
    conversation = BlockingConversation()
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=conversation,
    )
    await server.start()
    try:
        async with (
            connect(f"ws://{server.host}:{server.port}") as first,
            connect(f"ws://{server.host}:{server.port}") as second,
        ):
            auth = json.dumps(
                {
                    "version": 1,
                    "type": "auth",
                    "payload": {"token": "test-token"},
                }
            )
            await first.send(auth)
            await second.send(auth)
            await first.recv()
            await second.recv()

            await first.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": "request-first",
                        "payload": {"text": "first"},
                    }
                )
            )
            await asyncio.wait_for(conversation.started.wait(), timeout=0.5)

            await second.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": "request-second",
                        "payload": {"text": "second"},
                    }
                )
            )
            rejected = json.loads(
                await asyncio.wait_for(second.recv(), timeout=0.5)
            )

            assert rejected["type"] == "error"
            assert rejected["requestId"] == "request-second"
            assert rejected["payload"]["code"] == "busy"

            conversation.release.set()
            while True:
                event = json.loads(await asyncio.wait_for(first.recv(), timeout=0.5))
                if event["type"] == "state.changed" and event["payload"] == {
                    "state": "IDLE"
                }:
                    break
    finally:
        conversation.release.set()
        await server.stop()


async def test_invalid_chat_send_errors_preserve_extractable_request_ids() -> None:
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await websocket.recv()

            invalid_messages = [
                {
                    "version": 2,
                    "type": "chat.send",
                    "requestId": "wrong-version",
                    "payload": {"text": "hello"},
                },
                {
                    "version": 1,
                    "type": "chat.send",
                    "requestId": "wrong-envelope",
                    "payload": [],
                },
            ]
            for invalid in invalid_messages:
                await websocket.send(json.dumps(invalid))
                error = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=0.5)
                )
                assert error["type"] == "error"
                assert error["requestId"] == invalid["requestId"]
    finally:
        await server.stop()


async def test_disconnect_mid_reply_restores_idle_for_the_next_request() -> None:
    conversation = BlockingConversation()
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=conversation,
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as first:
            await first.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await first.recv()
            await first.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": "disconnected-request",
                        "payload": {"text": "disconnect"},
                    }
                )
            )
            await asyncio.wait_for(conversation.started.wait(), timeout=0.5)
            await first.close()

        conversation.release.set()
        for _ in range(50):
            if server.state == "IDLE":
                break
            await asyncio.sleep(0.01)
        assert server.state == "IDLE"

        async with connect(f"ws://{server.host}:{server.port}") as second:
            await second.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await second.recv()
            await second.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": "next-request",
                        "payload": {"text": "next"},
                    }
                )
            )

            events = []
            while True:
                event = json.loads(
                    await asyncio.wait_for(second.recv(), timeout=0.5)
                )
                events.append(event)
                if event["type"] == "state.changed" and event["payload"] == {
                    "state": "IDLE"
                }:
                    break

            assert events[0]["payload"] == {"state": "THINKING"}
            assert events[1]["payload"] == {"state": "RESPONDING"}
            assert events[-2]["type"] == "chat.completed"
    finally:
        conversation.release.set()
        await server.stop()


async def test_reply_failure_keeps_request_id_restores_idle_and_logs_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "failed-request"
    caplog.set_level(logging.ERROR)
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FailingConversation(),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await asyncio.wait_for(websocket.recv(), timeout=0.5)
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": request_id,
                        "payload": {"text": "fail"},
                    }
                )
            )

            events = [
                json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                for _ in range(3)
            ]

            assert [event["type"] for event in events] == [
                "state.changed",
                "error",
                "state.changed",
            ]
            assert events[-1]["payload"] == {"state": "IDLE"}
            assert all(event["requestId"] == request_id for event in events)
            assert "fake provider exploded" in caplog.text
    finally:
        await server.stop()


async def test_llm_failure_is_correlated_user_friendly_and_logged_without_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "deepseek-failed-request"
    caplog.set_level(logging.ERROR)
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=LLMFailingConversation(),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await websocket.recv()
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": request_id,
                        "payload": {"text": "sensitive-user-message"},
                    }
                )
            )

            events = [
                json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                for _ in range(3)
            ]

            assert [event["type"] for event in events] == [
                "state.changed",
                "error",
                "state.changed",
            ]
            assert all(event["requestId"] == request_id for event in events)
            assert events[1]["payload"] == {
                "code": "llm_bad_request",
                "message": "DeepSeek 拒绝了当前请求，请检查模型和请求配置。",
                "retryable": False,
            }
            assert events[2]["payload"] == {"state": "IDLE"}
            assert "request_id=deepseek-failed-request" in caplog.text
            assert "provider=deepseek" in caplog.text
            assert "status=400" in caplog.text
            assert "error_type=BadRequestError" in caplog.text
            assert "sensitive-user-message" not in caplog.text
            assert "DEEPSEEK_API_KEY" not in caplog.text
    finally:
        await server.stop()


async def test_agent_brain_failure_is_terminal_before_responding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FailingAgentRuntimeConversation(),
    )
    websocket = RecordingWebSocket()

    await server._run_chat(  # type: ignore[arg-type]
        websocket,
        "agent-failed-request",
        "private user request",
    )

    assert [event["type"] for event in websocket.events] == [
        "state.changed",
        "error",
        "state.changed",
    ]
    assert websocket.events[0]["payload"] == {"state": "THINKING"}
    assert websocket.events[1]["payload"] == {
        "code": "agent_runtime_unavailable",
        "message": "JARVIS 的能力决策暂时不可用，本次没有执行任何工具。",
        "retryable": True,
    }
    assert websocket.events[2]["payload"] == {"state": "IDLE"}
    assert all(
        event["requestId"] == "agent-failed-request"
        for event in websocket.events
    )
    assert "request_id=agent-failed-request" in caplog.text
    assert "stage=provider" in caplog.text
    assert "code=agent_brain_unavailable" in caplog.text
    assert "error_type=ProviderError" in caplog.text
    assert "private user request" not in caplog.text
    assert current_request_telemetry().is_noop


async def test_memory_failure_before_first_chunk_skips_responding_and_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "memory-failed-request"
    caplog.set_level(logging.ERROR)
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=MemoryFailingConversation(),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await websocket.recv()
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": request_id,
                        "payload": {"text": "secret pinned content"},
                    }
                )
            )

            events = [
                json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                for _ in range(3)
            ]

            assert [event["type"] for event in events] == [
                "state.changed",
                "error",
                "state.changed",
            ]
            assert events[0]["payload"] == {"state": "THINKING"}
            assert events[1]["payload"] == {
                "code": "memory_unavailable",
                "message": "本地长期记忆暂时不可用，请重启 JARVIS 后重试。",
                "retryable": True,
            }
            assert events[2]["payload"] == {"state": "IDLE"}
            assert all(event["requestId"] == request_id for event in events)
            assert "request_id=memory-failed-request" in caplog.text
            assert "operation=list_memories" in caplog.text
            assert "code=memory_unavailable" in caplog.text
            assert "error_type=OperationalError" in caplog.text
            assert "secret pinned content" not in caplog.text
    finally:
        await server.stop()


async def test_memory_failure_after_delta_keeps_responding_and_partial_delta() -> None:
    request_id = "partial-memory-failure"
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=PartialMemoryFailingConversation(),
    )
    await server.start()
    try:
        async with connect(f"ws://{server.host}:{server.port}") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "auth",
                        "payload": {"token": "test-token"},
                    }
                )
            )
            await websocket.recv()
            await websocket.send(
                json.dumps(
                    {
                        "version": 1,
                        "type": "chat.send",
                        "requestId": request_id,
                        "payload": {"text": "request"},
                    }
                )
            )

            events = [
                json.loads(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                for _ in range(5)
            ]

            assert [event["type"] for event in events] == [
                "state.changed",
                "state.changed",
                "chat.delta",
                "error",
                "state.changed",
            ]
            assert events[0]["payload"] == {"state": "THINKING"}
            assert events[1]["payload"] == {"state": "RESPONDING"}
            assert events[2]["payload"] == {"text": "visible partial"}
            assert events[3]["payload"]["code"] == "memory_unavailable"
            assert events[4]["payload"] == {"state": "IDLE"}
            assert all(event["requestId"] == request_id for event in events)
    finally:
        await server.stop()


async def test_process_stdout_is_lifecycle_only_and_logs_use_stderr(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment["JARVIS_AUTH_TOKEN"] = "subprocess-token"
    environment["JARVIS_DATA_DIR"] = str(tmp_path / "jarvis-data")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "jarvis_core",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        readiness_line = await asyncio.wait_for(process.stdout.readline(), timeout=2)
        readiness = json.loads(readiness_line)
        stderr_line = await asyncio.wait_for(process.stderr.readline(), timeout=2)

        assert readiness["type"] == "process.ready"
        assert readiness["port"] > 0
        assert readiness["protocolVersion"] == 1
        assert b"listening" in stderr_line
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=2)

    assert await process.stdout.read() == b""


async def test_incompatible_memory_schema_fails_before_process_ready(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "jarvis-data"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "memory.db") as connection:
        connection.execute(
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT)"
        )

    environment = os.environ.copy()
    environment["JARVIS_AUTH_TOKEN"] = "subprocess-token"
    environment["JARVIS_DATA_DIR"] = str(data_dir)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "jarvis_core",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3)

    assert process.returncode != 0
    assert stdout == b""
    assert b"incompatible_schema" in stderr


async def test_server_emits_one_sanitized_llm_perf_summary_with_first_delta(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_core.perf")
    conversation = make_llm_conversation(tmp_path)
    conversation.memory_store.remember("private pinned memory")
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=conversation,
    )
    websocket = RecordingWebSocket()

    await server._run_chat(  # type: ignore[arg-type]
        websocket,
        "perf-request",
        "private user text with DEEPSEEK_API_KEY=secret",
    )

    summaries = perf_summaries(caplog)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["request_id"] == "perf-request"
    assert summary["status"] == "success"
    assert summary["request_kind"] == "llm"
    assert isinstance(summary["first_delta_ms"], float)
    assert summary["first_delta_ms"] >= 0
    assert summary["pinned_memory_count"] == 1
    perf_text = next(
        record.getMessage()
        for record in caplog.records
        if record.name == "jarvis_core.perf"
    )
    assert "private pinned memory" not in perf_text
    assert "private user text" not in perf_text
    assert "secret" not in perf_text
    assert current_request_telemetry().is_noop


@pytest.mark.parametrize(
    ("command_text", "private_content"),
    [
        ("/remember private command argument", "private command argument"),
        ("记住，private natural memory", "private natural memory"),
    ],
)
async def test_server_memory_command_perf_has_no_llm_timing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    command_text: str,
    private_content: str,
) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_core.perf")
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=make_llm_conversation(tmp_path),
    )
    websocket = RecordingWebSocket()

    await server._run_chat(  # type: ignore[arg-type]
        websocket,
        "memory-command-request",
        command_text,
    )

    summaries = perf_summaries(caplog)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["status"] == "success"
    assert summary["request_kind"] == "memory_command"
    assert summary["command"] == "remember"
    assert "total_llm_ms" not in summary
    assert private_content not in str(summary)
    assert current_request_telemetry().is_noop


async def test_server_clear_all_requires_confirmation_and_reports_real_count(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_core.perf")
    router = ClearAllMemoryRouter()
    conversation = LLMConversation(
        PassthroughAgentRuntime(SingleChunkLLMClient()),
        personality_instructions="personality",
        capability_constraints="capabilities",
        memory_store=SQLiteMemoryStore(tmp_path / "memory.db"),
        memory_router=router,
    )
    conversation.memory_store.remember("one")
    conversation.memory_store.remember("two")
    server = JarvisCoreServer(auth_token="test-token", conversation=conversation)
    pending_socket = RecordingWebSocket()
    confirmation_socket = RecordingWebSocket()

    await server._run_chat(  # type: ignore[arg-type]
        pending_socket,
        "clear-pending-request",
        "清空所有长期记忆",
    )
    await server._run_chat(  # type: ignore[arg-type]
        confirmation_socket,
        "clear-confirm-request",
        "确认清空",
    )

    assert [event["type"] for event in pending_socket.events] == [
        "state.changed",
        "state.changed",
        "chat.delta",
        "chat.completed",
        "state.changed",
    ]
    assert "确认清空" in pending_socket.events[2]["payload"]["text"]
    assert confirmation_socket.events[2]["payload"] == {
        "text": "已清空 2 条长期记忆。"
    }
    assert all(
        event["requestId"] == "clear-confirm-request"
        for event in confirmation_socket.events
    )
    assert conversation.memory_store.list_memories() == ()
    assert router.calls == 1
    summaries = perf_summaries(caplog)
    assert [summary["request_kind"] for summary in summaries] == [
        "memory_command",
        "memory_command",
    ]
    assert [summary["command"] for summary in summaries] == [
        "clear_all",
        "clear_all",
    ]
    assert all("total_llm_ms" not in summary for summary in summaries)


async def test_server_clear_all_executor_failure_never_emits_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = ClearAllMemoryRouter()
    conversation = LLMConversation(
        PassthroughAgentRuntime(SingleChunkLLMClient()),
        personality_instructions="personality",
        capability_constraints="capabilities",
        memory_store=SQLiteMemoryStore(tmp_path / "memory.db"),
        memory_router=router,
    )
    saved = conversation.memory_store.remember("must remain").memory
    server = JarvisCoreServer(auth_token="test-token", conversation=conversation)
    await server._run_chat(  # type: ignore[arg-type]
        RecordingWebSocket(),
        "clear-pending-request",
        "清空所有长期记忆",
    )

    def fail_clear_all(expected_ids: tuple[int, ...]):
        del expected_ids
        raise MemoryStoreError(
            operation="clear_all",
            error_type="OperationalError",
        )

    monkeypatch.setattr(conversation.memory_store, "clear_all", fail_clear_all)
    websocket = RecordingWebSocket()
    await server._run_chat(  # type: ignore[arg-type]
        websocket,
        "clear-failed-request",
        "确认清空",
    )

    assert [event["type"] for event in websocket.events] == [
        "state.changed",
        "error",
        "state.changed",
    ]
    assert websocket.events[1]["payload"]["code"] == "memory_unavailable"
    assert all(event["requestId"] == "clear-failed-request" for event in websocket.events)
    assert conversation.memory_store.list_memories() == (saved,)


async def test_server_unsupported_memory_intent_is_local_memory_command(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_core.perf")
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=make_llm_conversation(tmp_path),
    )
    websocket = RecordingWebSocket()

    await server._run_chat(  # type: ignore[arg-type]
        websocket,
        "unsupported-memory-command-request",
        "删除关于 private ROS2 的记忆",
    )

    assert [event["type"] for event in websocket.events] == [
        "state.changed",
        "state.changed",
        "chat.delta",
        "chat.completed",
        "state.changed",
    ]
    assert websocket.events[2]["payload"] == {
        "text": "无法确定要删除的长期记忆 ID。请使用“删除记忆1”、"
        "“忘掉长期记忆 #1”或 /forget 1。"
    }
    summaries = perf_summaries(caplog)
    assert len(summaries) == 1
    assert summaries[0]["request_kind"] == "memory_command"
    assert summaries[0]["command"] == "forget"
    assert "private ROS2" not in str(summaries[0])


async def test_server_request_telemetry_does_not_leak_after_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_core.perf")
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FailingConversation(),
    )

    await server._run_chat(  # type: ignore[arg-type]
        RecordingWebSocket(),
        "error-request",
        "question",
    )

    summaries = perf_summaries(caplog)
    assert len(summaries) == 1
    assert summaries[0]["status"] == "error"
    assert summaries[0]["failure_phase"] == "request_handling"
    assert current_request_telemetry().is_noop


async def test_server_request_telemetry_does_not_leak_after_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_core.perf")
    conversation = BlockingConversation()
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=conversation,
    )

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await server._run_chat(  # type: ignore[arg-type]
                RecordingWebSocket(),
                "cancelled-request",
                "question",
            )

    summaries = perf_summaries(caplog)
    assert len(summaries) == 1
    assert summaries[0]["status"] == "error"
    assert current_request_telemetry().is_noop


async def test_telemetry_logging_failure_does_not_change_request_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis_core.telemetry as telemetry_module

    def broken_log(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("telemetry logging failed")

    monkeypatch.setattr(telemetry_module.LOGGER, "info", broken_log)
    server = JarvisCoreServer(
        auth_token="test-token",
        conversation=FakeConversation(chunk_delay=0),
    )
    websocket = RecordingWebSocket()

    await server._run_chat(  # type: ignore[arg-type]
        websocket,
        "best-effort-request",
        "question",
    )

    assert [event["type"] for event in websocket.events] == [
        "state.changed",
        "state.changed",
        "chat.delta",
        "chat.delta",
        "chat.delta",
        "chat.completed",
        "state.changed",
    ]
    assert current_request_telemetry().is_noop
