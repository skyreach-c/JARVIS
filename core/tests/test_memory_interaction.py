import asyncio
import logging
from pathlib import Path

import pytest

from jarvis_core.llm.client import LLMError
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.memory_interaction import (
    MEMORY_ROUTER_FAILURE_REPLY,
    MemoryInteractionCoordinator,
)
from jarvis_core.memory_router import (
    MemoryIntent,
    MemoryRouterError,
    MemoryRouterRequest,
)
from jarvis_core.memory_store import (
    ClearAllResult,
    MemoryStoreError,
    PinnedMemory,
    RememberResult,
    SQLiteMemoryStore,
)
from jarvis_core.telemetry import (
    RequestTelemetry,
    bind_request_telemetry,
    reset_request_telemetry,
)


class InMemoryStore:
    def __init__(self, memories: list[PinnedMemory] | None = None) -> None:
        self.memories = list(memories or [])
        self.list_calls = 0
        self.remember_calls: list[str] = []
        self.forget_calls: list[int] = []
        self.clear_all_calls: list[tuple[int, ...]] = []

    def remember(self, content: str) -> RememberResult:
        self.remember_calls.append(content)
        for memory in self.memories:
            if memory.content == content.strip():
                return RememberResult(memory=memory, created=False)
        memory = PinnedMemory(
            id=max((item.id for item in self.memories), default=0) + 1,
            content=content.strip(),
        )
        self.memories.append(memory)
        return RememberResult(memory=memory, created=True)

    def list_memories(self) -> tuple[PinnedMemory, ...]:
        self.list_calls += 1
        return tuple(sorted(self.memories, key=lambda item: item.id))

    def forget(self, memory_id: int) -> bool:
        self.forget_calls.append(memory_id)
        for index, memory in enumerate(self.memories):
            if memory.id == memory_id:
                del self.memories[index]
                return True
        return False

    def clear_all(self, expected_ids: tuple[int, ...]) -> ClearAllResult:
        self.clear_all_calls.append(expected_ids)
        current_ids = tuple(memory.id for memory in self.list_memories())
        if current_ids != expected_ids:
            return ClearAllResult(
                status="snapshot_changed",
                cleared_ids=(),
                cleared_count=0,
            )
        self.memories.clear()
        return ClearAllResult(
            status="cleared",
            cleared_ids=current_ids,
            cleared_count=len(current_ids),
        )


class FailingClearAllStore(InMemoryStore):
    def clear_all(self, expected_ids: tuple[int, ...]) -> ClearAllResult:
        self.clear_all_calls.append(expected_ids)
        raise MemoryStoreError(operation="clear_all", error_type="OperationalError")


class FakeRouter:
    def __init__(self, responses: list[MemoryIntent | BaseException]) -> None:
        self.responses = responses
        self.calls: list[MemoryRouterRequest] = []

    async def route(self, request: MemoryRouterRequest) -> MemoryIntent:
        self.calls.append(request)
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, BaseException):
            raise response
        return response


class MutatingRouter(FakeRouter):
    def __init__(self, store: InMemoryStore, response: MemoryIntent) -> None:
        super().__init__([response])
        self.store = store

    async def route(self, request: MemoryRouterRequest) -> MemoryIntent:
        response = await super().route(request)
        self.store.memories.clear()
        return response


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class TimedRouter(FakeRouter):
    def __init__(self, clock: ManualClock, response: MemoryIntent) -> None:
        super().__init__([response])
        self.clock = clock

    async def route(self, request: MemoryRouterRequest) -> MemoryIntent:
        self.clock.value = 0.040
        return await super().route(request)


def intent(
    action: str,
    *,
    content: str | None = None,
    memory_ids: list[int] | None = None,
    evidence: list[str] | None = None,
) -> MemoryIntent:
    return MemoryIntent.model_validate(
        {
            "action": action,
            "content": content,
            "memory_ids": memory_ids or [],
            "evidence": evidence or [],
        }
    )


async def test_fast_path_executes_locally_without_router() -> None:
    store = InMemoryStore()
    router = FakeRouter([])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("记住，alpha")

    assert reply == "已保存长期记忆 #1。"
    assert router.calls == []
    assert store.memories == [PinnedMemory(id=1, content="alpha")]


async def test_gate_false_keeps_ordinary_chat_on_existing_path() -> None:
    store = InMemoryStore([PinnedMemory(id=1, content="must remain")])
    router = FakeRouter([])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("你觉得自动驾驶适不适合我？")

    assert reply is None
    assert router.calls == []
    assert store.list_calls == 0


async def test_clear_all_intent_only_establishes_pending_without_deleting() -> None:
    store = InMemoryStore(
        [PinnedMemory(id=3, content="one"), PinnedMemory(id=7, content="two")]
    )
    router = FakeRouter([intent("clear_all")])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("清空所有长期记忆")

    assert reply == "这会删除当前全部 2 条长期记忆。请回复“确认清空”执行，或回复“取消”。"
    assert store.clear_all_calls == []
    assert [memory.id for memory in store.memories] == [3, 7]


async def test_pending_clear_all_confirmation_uses_real_executor_result() -> None:
    store = InMemoryStore(
        [PinnedMemory(id=3, content="one"), PinnedMemory(id=7, content="two")]
    )
    router = FakeRouter([intent("clear_all")])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("全部删除长期记忆")

    reply = await coordinator.handle("确认清空")

    assert reply == "已清空 2 条长期记忆。"
    assert store.clear_all_calls == [(3, 7)]
    assert store.memories == []
    assert len(router.calls) == 1


async def test_pending_clear_all_cancellation_is_local_and_keeps_store() -> None:
    memories = [PinnedMemory(id=3, content="one")]
    store = InMemoryStore(memories)
    router = FakeRouter([intent("clear_all")])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("这些长期记忆都不要了")

    reply = await coordinator.handle("取消")

    assert reply == "已取消本次长期记忆操作。"
    assert store.clear_all_calls == []
    assert store.memories == memories
    assert len(router.calls) == 1


async def test_pending_clear_all_rejects_other_input_without_router_or_chat() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="one")])
    router = FakeRouter([intent("clear_all")])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("清空所有长期记忆")

    reply = await coordinator.handle("先说说会有什么影响")

    assert reply == "当前正在等待清空长期记忆确认。请回复“确认清空”执行，或回复“取消”。"
    assert store.clear_all_calls == []
    assert len(router.calls) == 1


async def test_pending_clear_all_snapshot_change_fails_closed() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="one")])
    router = FakeRouter([intent("clear_all")])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("清空所有长期记忆")
    store.memories.append(PinnedMemory(id=7, content="added later"))

    reply = await coordinator.handle("确认")

    assert reply == "长期记忆列表已发生变化，没有执行清空。请重新发起清空请求。"
    assert store.clear_all_calls == [(3,)]
    assert [memory.id for memory in store.memories] == [3, 7]


async def test_pending_clear_all_executor_failure_propagates_without_success() -> None:
    store = FailingClearAllStore([PinnedMemory(id=3, content="one")])
    router = FakeRouter([intent("clear_all")])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("清空所有长期记忆")

    with pytest.raises(MemoryStoreError):
        await coordinator.handle("确认全部删除")

    assert store.memories == [PinnedMemory(id=3, content="one")]
    assert store.clear_all_calls == [(3,)]


async def test_clear_all_pending_is_ram_only_across_coordinator_restart() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="one")])
    first = MemoryInteractionCoordinator(store, FakeRouter([intent("clear_all")]))
    await first.handle("清空所有长期记忆")
    second_router = FakeRouter([])
    second = MemoryInteractionCoordinator(store, second_router)

    reply = await second.handle("确认清空")

    assert reply == "当前没有待确认的长期记忆清空操作。"
    assert second_router.calls == []
    assert store.clear_all_calls == []
    assert store.memories == [PinnedMemory(id=3, content="one")]


async def test_plain_confirmation_without_clear_pending_remains_ordinary_chat() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="must remain")])
    router = FakeRouter([])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("确认")

    assert reply is None
    assert router.calls == []
    assert store.clear_all_calls == []


@pytest.mark.parametrize(
    "router_result",
    [
        intent("chat"),
        MemoryRouterError(
            stage="router_parse",
            code="invalid_schema",
            error_type="missing_required_field",
        ),
    ],
)
async def test_clear_all_safety_domain_never_falls_back_to_chat(
    router_result: MemoryIntent | BaseException,
) -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="must remain")])
    router = FakeRouter([router_result])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("清空所有长期记忆")

    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert store.clear_all_calls == []
    assert store.memories == [PinnedMemory(id=3, content="must remain")]


async def test_forget_candidate_router_chat_arms_one_shot_id_guard() -> None:
    store = InMemoryStore([PinnedMemory(id=17, content="must remain")])
    router = FakeRouter([intent("chat")])
    coordinator = MemoryInteractionCoordinator(store, router)

    first_reply = await coordinator.handle("删除 ROS2 那条")
    guarded_reply = await coordinator.handle("#17")

    assert first_reply == MEMORY_ROUTER_FAILURE_REPLY
    assert "没有已确认" in guarded_reply
    assert len(router.calls) == 1
    assert store.forget_calls == []


@pytest.mark.parametrize("text", ["17", "#17", "第17条"])
async def test_id_like_text_without_pending_or_guard_remains_ordinary_chat(
    text: str,
) -> None:
    store = InMemoryStore()
    router = FakeRouter([])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(text)

    assert reply is None
    assert router.calls == []
    assert store.forget_calls == []


async def test_semantic_remember_requires_exact_user_evidence_before_execution() -> None:
    text = "我以后主要往自动驾驶方向走，这个你记一下"
    store = InMemoryStore()
    router = FakeRouter(
        [
            intent(
                "remember",
                content="我主要往自动驾驶方向发展",
                evidence=["我以后主要往自动驾驶方向走"],
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(text)

    assert reply == "已保存长期记忆 #1。"
    assert store.remember_calls == ["我主要往自动驾驶方向发展"]


async def test_real_gate_regression_routes_long_term_remember_wording() -> None:
    text = "我以后主要往机器人视觉方向发展，这件事帮我长期记下来"
    store = InMemoryStore()
    router = FakeRouter(
        [intent("remember", content="我以后主要往机器人视觉方向发展")]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(text)

    assert reply == "已保存长期记忆 #1。"
    assert len(router.calls) == 1
    assert store.remember_calls == ["我以后主要往机器人视觉方向发展"]


async def test_reference_remember_accepts_exact_recent_message_without_evidence() -> None:
    store = InMemoryStore()
    router = FakeRouter([intent("remember", content="我正在测试双目相机")])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(
        "刚才那个你帮我长期记一下",
        recent_user_messages=("我正在测试双目相机",),
    )

    assert reply == "已保存长期记忆 #1。"
    assert store.remember_calls == ["我正在测试双目相机"]


async def test_semantic_remember_rejects_unverifiable_evidence() -> None:
    store = InMemoryStore()
    router = FakeRouter(
        [
            intent(
                "remember",
                content="用户精通 ROS2",
                evidence=["用户精通 ROS2"],
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("我刚开始学习 ROS2，这个记一下")

    assert "没有执行" in reply
    assert store.remember_calls == []
    assert store.memories == []


async def test_unverified_remember_does_not_arm_delete_id_follow_up_guard() -> None:
    store = InMemoryStore()
    router = FakeRouter([intent("remember", content="invented fact")])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("刚才那个你帮我长期记一下")

    reply = await coordinator.handle("#17")

    assert reply is None
    assert len(router.calls) == 1
    assert store.forget_calls == []


async def test_semantic_remember_without_evidence_rejects_non_source_content() -> None:
    store = InMemoryStore()
    router = FakeRouter([intent("remember", content="用户精通 ROS2")])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("我刚开始学习 ROS2，这个记一下")

    assert "没有执行" in reply
    assert store.remember_calls == []


async def test_remember_evidence_cannot_reach_beyond_router_session_window() -> None:
    store = InMemoryStore()
    router = FakeRouter(
        [
            intent(
                "remember",
                content="old fact",
                evidence=["old fact"],
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(
        "刚才那个你长期记一下",
        recent_user_messages=("old fact", "middle", "latest"),
    )

    assert "没有执行" in reply
    assert router.calls[0].recent_user_messages == ("middle", "latest")
    assert store.remember_calls == []


async def test_semantic_forget_executes_only_one_existing_id() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="学习 ROS2")])
    router = FakeRouter([intent("forget", memory_ids=[3])])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("把 ROS2 那条删了")

    assert reply == "已删除长期记忆 #3。"
    assert store.forget_calls == [3]
    assert store.memories == []


@pytest.mark.parametrize(
    "text",
    [
        "把双目相机那条记忆删了",
        "把 ROS2 那条删了",
        "刚保存那个删了",
    ],
)
async def test_real_semantic_forget_regressions_use_executor(text: str) -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="目标记录")])
    router = FakeRouter([intent("forget", memory_ids=[3])])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(text)

    assert reply == "已删除长期记忆 #3。"
    assert store.forget_calls == [3]


async def test_fictitious_router_id_never_reaches_executor() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="学习 ROS2")])
    router = FakeRouter([intent("forget", memory_ids=[99])])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("把 ROS2 那条删了")

    assert "没有执行" in reply
    assert store.forget_calls == []
    assert store.memories == [PinnedMemory(id=3, content="学习 ROS2")]


async def test_forget_without_ids_fails_core_validation_without_executor() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="学习 ROS2")])
    router = FakeRouter([intent("forget")])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("把 ROS2 那条删了")

    assert "没有执行" in reply
    assert store.forget_calls == []
    assert store.memories == [PinnedMemory(id=3, content="学习 ROS2")]


@pytest.mark.parametrize(
    "routed_intent",
    [
        intent("forget", memory_ids=[0]),
        intent("forget", memory_ids=[3, 3]),
        intent("remember", content="  ", evidence=["source"]),
        intent("remember", content="value", memory_ids=[3], evidence=["value"]),
        intent("clear_all", memory_ids=[3]),
    ],
)
async def test_action_specific_core_validation_rejects_unsafe_intent_shapes(
    routed_intent: MemoryIntent,
) -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="must remain")])
    router = FakeRouter([routed_intent])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("把这条长期记忆删了")

    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert store.remember_calls == []
    assert store.forget_calls == []
    assert store.memories == [PinnedMemory(id=3, content="must remain")]


async def test_forget_revalidates_store_after_router_to_close_race() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="学习 ROS2")])
    router = MutatingRouter(store, intent("forget", memory_ids=[3]))
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("把 ROS2 那条删了")

    assert "没有执行" in reply
    assert store.forget_calls == []


async def test_multiple_forget_candidates_require_id_then_resolve_locally() -> None:
    store = InMemoryStore(
        [
            PinnedMemory(id=3, content="正在学习 ROS2"),
            PinnedMemory(id=7, content="机器人项目使用 ROS2"),
        ]
    )
    router = FakeRouter([intent("clarify", memory_ids=[3, 7])])
    coordinator = MemoryInteractionCoordinator(store, router)

    clarification = await coordinator.handle("把 ROS2 那条删了")
    reply = await coordinator.handle("#3")

    assert clarification.startswith("找到多条可能的长期记忆")
    assert "#3" in clarification and "#7" in clarification
    assert reply == "已删除长期记忆 #3。"
    assert len(router.calls) == 1
    assert store.forget_calls == [3]
    assert store.memories == [PinnedMemory(id=7, content="机器人项目使用 ROS2")]


async def test_real_ros2_multi_candidate_phrase_clarifies_then_deletes_choice() -> None:
    store = InMemoryStore(
        [
            PinnedMemory(id=3, content="正在学习 ROS2"),
            PinnedMemory(id=7, content="机器人项目使用 ROS2"),
        ]
    )
    router = FakeRouter([intent("clarify", memory_ids=[3, 7])])
    coordinator = MemoryInteractionCoordinator(store, router)

    clarification = await coordinator.handle("删除 ROS2 那条")
    reply = await coordinator.handle("第7条")

    assert clarification.startswith("找到多条可能的长期记忆")
    assert reply == "已删除长期记忆 #7。"
    assert store.forget_calls == [7]
    assert store.memories == [PinnedMemory(id=3, content="正在学习 ROS2")]


async def test_non_candidate_pending_id_is_local_noop_and_keeps_pending() -> None:
    store = InMemoryStore(
        [PinnedMemory(id=3, content="one"), PinnedMemory(id=7, content="two")]
    )
    router = FakeRouter([intent("clarify", memory_ids=[3, 7])])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("把刚才那条长期记忆删掉")

    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry(
        "invalid-pending-id",
        summary_sink=summaries.append,
    )
    token = bind_request_telemetry(telemetry)
    try:
        invalid_reply = await coordinator.handle("#17")
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)
    deleted_reply = await coordinator.handle("3")

    assert "候选" in invalid_reply
    assert summaries[0]["request_kind"] == "memory_command"
    assert summaries[0]["command"] == "clarify"
    assert deleted_reply == "已删除长期记忆 #3。"
    assert len(router.calls) == 1
    assert store.forget_calls == [3]


async def test_pending_clarification_can_be_cancelled_without_side_effect() -> None:
    store = InMemoryStore(
        [PinnedMemory(id=3, content="one"), PinnedMemory(id=7, content="two")]
    )
    router = FakeRouter([intent("clarify", memory_ids=[3, 7])])
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("把刚才那条长期记忆删掉")

    reply = await coordinator.handle("算了")

    assert reply == "已取消本次长期记忆操作。"
    assert store.forget_calls == []
    assert len(router.calls) == 1


async def test_generic_clarification_never_assumes_a_delete_operation() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="one")])
    router = FakeRouter([intent("clarify")])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle("刚才那个你长期记一下")

    assert "没有执行" in reply
    assert "保存的内容" in reply
    assert store.remember_calls == []
    assert store.forget_calls == []


async def test_router_failure_retains_pending_candidates_for_local_id_retry() -> None:
    store = InMemoryStore(
        [PinnedMemory(id=3, content="one"), PinnedMemory(id=7, content="two")]
    )
    router = FakeRouter(
        [
            intent("clarify", memory_ids=[3, 7]),
            LLMError(
                code="llm_timeout",
                user_message="timeout",
                retryable=True,
                provider="deepseek",
            ),
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)
    await coordinator.handle("把刚才那条长期记忆删掉")

    failed_reply = await coordinator.handle("还是刚才那个")
    deleted_reply = await coordinator.handle("第3条")

    assert failed_reply == MEMORY_ROUTER_FAILURE_REPLY
    assert deleted_reply == "已删除长期记忆 #3。"
    assert len(router.calls) == 2
    assert store.forget_calls == [3]


async def test_router_failure_guards_hash_id_follow_up_without_pending() -> None:
    store = InMemoryStore([PinnedMemory(id=17, content="must remain")])
    router = FakeRouter(
        [
            MemoryRouterError(
                stage="router_parse",
                code="invalid_schema",
                error_type="missing",
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    failed_reply = await coordinator.handle("把 ROS2 那条删了")
    guarded_reply = await coordinator.handle("#17")
    ordinary_reply = await coordinator.handle("17")

    assert failed_reply == MEMORY_ROUTER_FAILURE_REPLY
    assert "没有已确认" in guarded_reply
    assert ordinary_reply is None
    assert len(router.calls) == 1
    assert store.forget_calls == []
    assert store.memories == [PinnedMemory(id=17, content="must remain")]


async def test_router_failure_is_local_fail_closed_and_does_not_call_store() -> None:
    private_text = "把 private ROS2 那条删了"
    store = InMemoryStore([PinnedMemory(id=3, content="private memory")])
    router = FakeRouter(
        [
            LLMError(
                code="llm_timeout",
                user_message="provider timeout",
                retryable=True,
                provider="deepseek",
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(private_text)

    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert store.forget_calls == []
    assert store.remember_calls == []
    assert "private" not in reply


async def test_router_cancellation_propagates_without_side_effect() -> None:
    store = InMemoryStore([PinnedMemory(id=3, content="must remain")])
    router = FakeRouter([asyncio.CancelledError()])
    coordinator = MemoryInteractionCoordinator(store, router)

    with pytest.raises(asyncio.CancelledError):
        await coordinator.handle("把 ROS2 那条删了")

    assert store.forget_calls == []
    assert store.memories == [PinnedMemory(id=3, content="must remain")]


async def test_recent_memory_context_is_ram_only_and_derived_from_execution() -> None:
    store = InMemoryStore()
    first_router = FakeRouter([intent("forget", memory_ids=[1])])
    first = MemoryInteractionCoordinator(store, first_router)
    await first.handle("记住，alpha")
    await first.handle("刚保存那个删了")

    assert first_router.calls[0].last_created_memory_id == 1

    second_router = FakeRouter([intent("chat")])
    second = MemoryInteractionCoordinator(store, second_router)
    reply = await second.handle("刚保存那个删了")

    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert second_router.calls[0].last_created_memory_id is None


async def test_list_fast_path_supplies_only_real_listed_ids_to_router() -> None:
    store = InMemoryStore(
        [PinnedMemory(id=3, content="one"), PinnedMemory(id=7, content="two")]
    )
    router = FakeRouter([intent("chat")])
    coordinator = MemoryInteractionCoordinator(store, router)

    await coordinator.handle("查看长期记忆")
    await coordinator.handle("刚才列出的那些不用长期记了")

    assert router.calls[0].last_listed_memory_ids == (3, 7)


async def test_router_receives_only_two_latest_user_session_messages() -> None:
    store = InMemoryStore()
    router = FakeRouter([intent("chat")])
    coordinator = MemoryInteractionCoordinator(store, router)

    reply = await coordinator.handle(
        "刚才那个你长期记一下",
        recent_user_messages=("old", "middle", "latest"),
    )

    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert router.calls[0].recent_user_messages == ("middle", "latest")


async def test_semantic_remember_reuses_existing_length_and_capacity_rules(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db", max_memories=1)
    store.remember("existing")
    overlong = "x" * 501
    router = FakeRouter(
        [
            intent("remember", content=overlong, evidence=[overlong]),
            intent("remember", content="second", evidence=["second"]),
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    length_reply = await coordinator.handle(f"{overlong}，这个你记一下")
    capacity_reply = await coordinator.handle("second，这个你记一下")

    assert length_reply == "长期记忆内容不能超过 500 个字符。"
    assert capacity_reply.startswith("长期记忆已达到 1 条上限")
    assert [memory.content for memory in store.list_memories()] == ["existing"]


async def test_semantic_router_timing_is_recorded_without_sensitive_data() -> None:
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    store = InMemoryStore([PinnedMemory(id=3, content="private-memory")])
    router = TimedRouter(clock, intent("forget", memory_ids=[3]))
    router_profile = ModelProfile(
        name="structured_router",
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    coordinator = MemoryInteractionCoordinator(
        store,
        router,
        router_profile=router_profile,
    )
    telemetry = RequestTelemetry(
        "router-request",
        clock=clock,
        summary_sink=summaries.append,
    )
    token = bind_request_telemetry(telemetry)
    try:
        await coordinator.handle("把 private-memory 那条删了")
        clock.value = 0.050
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)

    summary = summaries[0]
    assert summary["request_kind"] == "memory_command"
    assert summary["command"] == "forget"
    assert summary["memory_router_ms"] == 40.0
    assert summary["memory_router_action"] == "forget"
    assert summary["memory_router_profile"] == "structured_router"
    assert summary["memory_router_provider"] == "deepseek"
    assert summary["memory_router_model"] == "deepseek-v4-flash"
    assert "profile" not in summary
    assert "provider" not in summary
    assert "model" not in summary
    assert "private-memory" not in str(summary)


async def test_router_failure_log_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_text = "把 secret-user-text 那条删了"
    private_memory = "secret-pinned-memory"
    private_key = "redacted-provider-secret"
    store = InMemoryStore([PinnedMemory(id=3, content=private_memory)])
    router = FakeRouter(
        [
            LLMError(
                code="llm_provider_error",
                user_message=f"response body {private_key}",
                retryable=False,
                provider="deepseek",
                error_type="PrivateProviderError",
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)

    telemetry = RequestTelemetry("safe-request-id")
    token = bind_request_telemetry(telemetry)
    try:
        with caplog.at_level(logging.WARNING, logger="jarvis_core.memory_interaction"):
            reply = await coordinator.handle(private_text)
    finally:
        reset_request_telemetry(token)

    log_text = caplog.text
    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert "request_id=safe-request-id" in log_text
    assert "stage=provider" in log_text
    assert "category=provider_error" in log_text
    assert "reason=provider_failure" in log_text
    assert "fields=none" in log_text
    assert "PrivateProviderError" in log_text
    assert private_text not in log_text
    assert private_memory not in log_text
    assert private_key not in log_text


async def test_router_parse_failure_log_has_sanitized_stage_and_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_text = "把 secret-memory 那条删了"
    store = InMemoryStore([PinnedMemory(id=3, content="secret-memory")])
    router = FakeRouter(
        [
            MemoryRouterError(
                stage="router_parse",
                code="invalid_action",
                error_type="literal_mismatch",
                safe_reason_enum="unknown_action",
                safe_field_names=("action",),
            )
        ]
    )
    coordinator = MemoryInteractionCoordinator(store, router)
    telemetry = RequestTelemetry("router-parse-request")
    token = bind_request_telemetry(telemetry)
    try:
        with caplog.at_level(logging.WARNING, logger="jarvis_core.memory_interaction"):
            reply = await coordinator.handle(private_text)
    finally:
        reset_request_telemetry(token)

    log_text = caplog.text
    assert reply == MEMORY_ROUTER_FAILURE_REPLY
    assert "request_id=router-parse-request" in log_text
    assert "stage=router_parse" in log_text
    assert "category=invalid_action" in log_text
    assert "reason=unknown_action" in log_text
    assert "fields=action" in log_text
    assert "error_type=literal_mismatch" in log_text
    assert private_text not in log_text
    assert "secret-memory" not in log_text


async def test_core_evidence_failure_log_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_text = "我刚开始学习 secret ROS2，这个记一下"
    store = InMemoryStore()
    router = FakeRouter([intent("remember", content="用户精通 secret ROS2")])
    coordinator = MemoryInteractionCoordinator(store, router)
    telemetry = RequestTelemetry("core-validation-request")
    token = bind_request_telemetry(telemetry)
    try:
        with caplog.at_level(logging.WARNING, logger="jarvis_core.memory_interaction"):
            reply = await coordinator.handle(private_text)
    finally:
        reset_request_telemetry(token)

    log_text = caplog.text
    assert "没有执行" in reply
    assert "request_id=core-validation-request" in log_text
    assert "stage=core_validation" in log_text
    assert "category=evidence_validation" in log_text
    assert "reason=unverified_evidence" in log_text
    assert "fields=content,evidence" in log_text
    assert private_text not in log_text
    assert "secret" not in log_text
