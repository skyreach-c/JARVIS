import asyncio
import io
import json
import logging
import os
import sys
from collections.abc import AsyncIterator

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from jarvis_core.conversation import FakeConversation
from jarvis_core.server import JarvisCoreServer, emit_process_ready


class FailingConversation:
    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        if False:
            yield ""
        raise RuntimeError("fake provider exploded")


class BlockingConversation:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_reply(self, text: str) -> AsyncIterator[str]:
        del text
        self.started.set()
        await self.release.wait()
        yield "done"


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
            assert "".join(chunks) == "晚上好。JARVIS Core 已上线。"
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
                for _ in range(4)
            ]

            assert [event["type"] for event in events] == [
                "state.changed",
                "state.changed",
                "error",
                "state.changed",
            ]
            assert events[-1]["payload"] == {"state": "IDLE"}
            assert all(event["requestId"] == request_id for event in events)
            assert "fake provider exploded" in caplog.text
    finally:
        await server.stop()


async def test_process_stdout_is_lifecycle_only_and_logs_use_stderr() -> None:
    environment = os.environ.copy()
    environment["JARVIS_AUTH_TOKEN"] = "subprocess-token"
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
