import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class ClientMessage(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    version: int
    message_type: str = Field(alias="type")
    request_id: str | None = Field(default=None, alias="requestId")
    payload: dict[str, Any] = Field(default_factory=dict)


def parse_client_message(raw: str) -> ClientMessage:
    request_id = _extract_request_id(raw)
    try:
        message = ClientMessage.model_validate_json(raw)
    except ValidationError as exc:
        raise ProtocolError("invalid message envelope", request_id=request_id) from exc
    if message.version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version", request_id=request_id)
    if message.message_type == "chat.send" and not message.request_id:
        raise ProtocolError("chat.send requires requestId")
    return message


def _extract_request_id(raw: str) -> str | None:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict) or decoded.get("type") != "chat.send":
        return None
    request_id = decoded.get("requestId")
    return request_id if isinstance(request_id, str) and request_id else None


def build_server_message(
    message_type: str,
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
) -> str:
    message: dict[str, Any] = {
        "version": PROTOCOL_VERSION,
        "type": message_type,
        "payload": payload,
    }
    if request_id is not None:
        message["requestId"] = request_id
    return json.dumps(message, separators=(",", ":"))
