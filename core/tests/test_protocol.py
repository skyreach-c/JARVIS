import json

import pytest

from jarvis_core.protocol import ProtocolError, build_server_message, parse_client_message


def test_parse_chat_send_preserves_request_id() -> None:
    request_id = "request-123"
    raw = json.dumps(
        {
            "version": 1,
            "type": "chat.send",
            "requestId": request_id,
            "payload": {"text": "你好"},
        }
    )

    message = parse_client_message(raw)

    assert message.request_id == request_id
    assert message.payload == {"text": "你好"}


def test_chat_send_requires_request_id() -> None:
    raw = json.dumps({"version": 1, "type": "chat.send", "payload": {"text": "hello"}})

    with pytest.raises(ProtocolError, match="requestId"):
        parse_client_message(raw)


def test_protocol_rejects_unsupported_version() -> None:
    raw = json.dumps(
        {
            "version": 2,
            "type": "auth",
            "payload": {"token": "local-token"},
        }
    )

    with pytest.raises(ProtocolError, match="version"):
        parse_client_message(raw)


def test_invalid_chat_send_exposes_its_request_id_for_error_correlation() -> None:
    raw = json.dumps(
        {
            "version": 2,
            "type": "chat.send",
            "requestId": "invalid-request",
            "payload": {"text": "hello"},
        }
    )

    with pytest.raises(ProtocolError) as raised:
        parse_client_message(raw)

    assert raised.value.request_id == "invalid-request"


def test_server_message_preserves_request_id() -> None:
    raw = build_server_message(
        "state.changed",
        {"state": "THINKING"},
        request_id="request-456",
    )

    message = json.loads(raw)

    assert message["requestId"] == "request-456"
    assert message["type"] == "state.changed"
