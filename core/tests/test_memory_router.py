import json
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from jarvis_core.llm.client import ChatMessage
from jarvis_core.memory_router import (
    MemoryIntent,
    MemoryRouterError,
    MemoryRouterRequest,
    PendingClarification,
    SemanticMemoryIntentRouter,
    classify_potential_memory_intent,
    is_potential_memory_intent,
)
from jarvis_core.memory_store import PinnedMemory


class RecordingStructuredClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[tuple[ChatMessage, ...], int]] = []

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
    ) -> str:
        snapshot = tuple(dict(message) for message in messages)
        self.calls.append((snapshot, max_tokens))
        return self.response


@pytest.mark.parametrize(
    "text",
    [
        "把 ROS2 那条删了",
        "把双目相机那条记忆删了",
        "刚才那个不用长期记了",
        "刚保存那个删了",
        "我以后主要往自动驾驶和机器人方向走，这个你记一下",
        "我以后主要往机器人视觉方向发展，这件事帮我长期记下来",
        "你现在长期记得我些什么？",
    ],
)
def test_gate_accepts_semantic_memory_candidates(text: str) -> None:
    assert is_potential_memory_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "清空所有长期记忆",
        "全部删除长期记忆",
        "这些长期记忆都不要了",
    ],
)
def test_gate_classifies_explicit_clear_all_candidates(text: str) -> None:
    assert classify_potential_memory_intent(text) == "clear_all"
    assert is_potential_memory_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "我最近主要学习 ROS2。",
        "你觉得自动驾驶适不适合我？",
        "人为什么会忘记东西？",
        "长期记忆系统应该怎么设计？",
        "长期保存数据有什么风险？",
        "JARVIS以后应该怎么设计记忆？",
        "删除长期记忆是不是危险操作？",
        "清空缓存是什么意思？",
    ],
)
def test_gate_keeps_ordinary_discussion_on_chat_path(text: str) -> None:
    assert is_potential_memory_intent(text) is False


def test_pending_clarification_routes_non_empty_follow_up_to_router() -> None:
    assert is_potential_memory_intent("第二条", pending_clarification=True) is True
    assert is_potential_memory_intent("  ", pending_clarification=True) is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "chat",
            "content": None,
            "memory_ids": [],
            "evidence": [],
        },
        {
            "action": "remember",
            "content": "我主要学习 ROS2",
            "memory_ids": [],
            "evidence": ["我主要学习 ROS2"],
        },
        {
            "action": "forget",
            "content": None,
            "memory_ids": [3],
            "evidence": [],
        },
        {
            "action": "clarify",
            "content": None,
            "memory_ids": [3, 7],
            "evidence": [],
        },
        {
            "action": "clear_all",
            "content": None,
            "memory_ids": [],
            "evidence": [],
        },
    ],
)
def test_memory_intent_accepts_only_consistent_shapes(payload: dict[str, object]) -> None:
    intent = MemoryIntent.model_validate(payload)

    assert intent.model_dump() == payload


@pytest.mark.parametrize(
    "action",
    ["chat", "list", "forget", "clarify", "clear_all"],
)
def test_memory_intent_applies_safe_wire_defaults(action: str) -> None:
    intent = MemoryIntent.model_validate({"action": action})

    assert intent.content is None
    assert intent.memory_ids == []
    assert intent.evidence == []


def test_memory_intent_allows_missing_remember_evidence_for_core_validation() -> None:
    intent = MemoryIntent.model_validate(
        {"action": "remember", "content": "我主要学习 ROS2"}
    )

    assert intent.evidence == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "unknown",
            "content": None,
            "memory_ids": [],
            "evidence": [],
        },
        {
            "action": "forget",
            "content": None,
            "memory_ids": [True],
            "evidence": [],
        },
        {
            "action": "chat",
            "content": None,
            "memory_ids": [],
            "evidence": [],
            "extra": "forbidden",
        },
    ],
)
def test_memory_intent_rejects_structurally_invalid_wire_output(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        MemoryIntent.model_validate(payload)


async def test_semantic_router_sends_only_explicit_bounded_context() -> None:
    client = RecordingStructuredClient(
        '{"action":"forget","content":null,"memory_ids":[3],"evidence":[]}'
    )
    router = SemanticMemoryIntentRouter(client)
    request = MemoryRouterRequest(
        text="把 ROS2 那条删了",
        memories=(
            PinnedMemory(id=3, content="我正在学习 ROS2"),
            PinnedMemory(id=7, content="机器人项目使用 ROS2"),
        ),
        recent_user_messages=("我最近在调机器人", "保留原始用户文本"),
        last_created_memory_id=7,
        last_listed_memory_ids=(3, 7),
        pending_clarification=PendingClarification(
            action="forget",
            candidate_ids=(3, 7),
        ),
    )

    result = await router.route(request)

    assert result.action == "forget"
    messages, max_tokens = client.calls[0]
    assert max_tokens == 1024
    assert [message["role"] for message in messages] == ["system", "user"]
    system_prompt = messages[0]["content"]
    assert "JSON" in system_prompt
    assert all(
        field in system_prompt
        for field in ("action", "content", "memory_ids", "evidence")
    )
    assert '"action":"chat"' in system_prompt
    assert '"action":"list"' in system_prompt
    assert '"action":"remember"' in system_prompt
    assert '"action":"forget"' in system_prompt
    assert '"action":"clarify"' in system_prompt
    assert '"action":"clear_all"' in system_prompt
    assert "six action names shown in the schema" in system_prompt
    assert "刚保存那个删了" in system_prompt
    assert "last_created_memory_id" in system_prompt
    assert "刚才那个你帮我长期记一下" in system_prompt
    payload = json.loads(messages[1]["content"])
    assert payload == {
        "current_user_text": "把 ROS2 那条删了",
        "pinned_memories": [
            {"id": 3, "content": "我正在学习 ROS2"},
            {"id": 7, "content": "机器人项目使用 ROS2"},
        ],
        "recent_user_messages": ["我最近在调机器人", "保留原始用户文本"],
        "recent_memory_interaction": {
            "last_created_memory_id": 7,
            "last_listed_memory_ids": [3, 7],
            "pending_clarification": {
                "action": "forget",
                "candidate_ids": [3, 7],
            },
        },
    }
    assert "Identity / Personality" not in system_prompt
    assert "Current Runtime Capabilities" not in system_prompt


@pytest.mark.parametrize(
    (
        "raw_response",
        "expected_code",
        "expected_stage",
        "expected_reason",
        "expected_fields",
    ),
    [
        ("not json", "invalid_json", "router_parse", "invalid_json", ()),
        (
            "{}",
            "invalid_schema",
            "router_parse",
            "missing_required_field",
            ("action",),
        ),
        (
            '{"action":"delete","content":null,"memory_ids":[1],"evidence":[]}',
            "invalid_action",
            "router_parse",
            "unknown_action",
            ("action",),
        ),
        (
            '{"action":[],"content":null,"memory_ids":[],"evidence":[]}',
            "invalid_action",
            "router_parse",
            "unknown_action",
            ("action",),
        ),
        (
            '{"action":"forget","content":null,"memory_ids":[true],"evidence":[]}',
            "id_validation",
            "router_parse",
            "invalid_id_value",
            ("memory_ids",),
        ),
    ],
)
async def test_semantic_router_fails_closed_on_invalid_output(
    raw_response: str,
    expected_code: str,
    expected_stage: str,
    expected_reason: str,
    expected_fields: tuple[str, ...],
) -> None:
    router = SemanticMemoryIntentRouter(RecordingStructuredClient(raw_response))
    request = MemoryRouterRequest(text="删除它", memories=())

    with pytest.raises(MemoryRouterError) as raised:
        await router.route(request)

    assert raised.value.code == expected_code
    assert raised.value.stage == expected_stage
    assert raised.value.safe_reason_enum == expected_reason
    assert raised.value.safe_field_names == expected_fields
    assert raw_response not in str(raised.value)


async def test_semantic_router_sanitizes_unknown_extra_field_name() -> None:
    private_field_name = "secret_memory_text"
    raw_response = (
        '{"action":"chat","content":null,"memory_ids":[],"evidence":[],'
        f'"{private_field_name}":"private"}}'
    )
    router = SemanticMemoryIntentRouter(RecordingStructuredClient(raw_response))

    with pytest.raises(MemoryRouterError) as raised:
        await router.route(MemoryRouterRequest(text="删除那条", memories=()))

    assert raised.value.code == "invalid_schema"
    assert raised.value.safe_reason_enum == "extra_field"
    assert raised.value.safe_field_names == ("root",)
    assert private_field_name not in str(raised.value)


async def test_semantic_router_accepts_safe_missing_wire_fields() -> None:
    router = SemanticMemoryIntentRouter(
        RecordingStructuredClient(
            '{"action":"remember","content":"我主要学习 ROS2"}'
        )
    )

    result = await router.route(
        MemoryRouterRequest(text="这件事帮我长期记下来", memories=())
    )

    assert result.model_dump() == {
        "action": "remember",
        "content": "我主要学习 ROS2",
        "memory_ids": [],
        "evidence": [],
    }
