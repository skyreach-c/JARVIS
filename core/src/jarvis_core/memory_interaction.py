from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from jarvis_core.llm.client import LLMError
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.memory_commands import (
    MemoryExecutionResult,
    ParsedMemoryCommand,
    UnsupportedMemoryCommand,
    execute_memory_command_route_result,
    execute_parsed_memory_command_result,
    route_memory_command,
)
from jarvis_core.memory_router import (
    MemoryIntent,
    MemoryIntentRouter,
    MemoryRouterError,
    MemoryRouterRequest,
    PendingClarification,
    PotentialMemoryAction,
    classify_potential_memory_intent,
)
from jarvis_core.memory_store import MemoryStore, PinnedMemory
from jarvis_core.telemetry import FailurePhase, current_request_telemetry

logger = logging.getLogger(__name__)

MEMORY_ROUTER_FAILURE_REPLY = (
    "这次没能可靠确认你的记忆操作，因此没有执行任何修改。"
    "你可以换个说法再试。"
)
_UNVERIFIED_REMEMBER_REPLY = (
    "这次没能可靠确认要保存的内容，因此没有执行。"
    "请明确说“记住，<内容>”。"
)
_UNRESOLVED_FORGET_REPLY = (
    "这次没能唯一确认要删除的长期记忆，因此没有执行。"
    "请先查看长期记忆，再提供明确的 #ID。"
)
_GENERIC_CLARIFICATION_REPLY = (
    "这次没能可靠确认你的长期记忆操作，因此没有执行。"
    "请明确说明要保存的内容，或提供要删除的 #ID。"
)
_CANCEL_REPLY = "已取消本次长期记忆操作。"
_INVALID_PENDING_SELECTION_REPLY = (
    "这个 ID 不在当前待确认的候选中，没有执行删除。请回复候选列表中的 #ID。"
)
_NO_PENDING_SELECTION_REPLY = (
    "当前没有已确认的长期记忆候选，因此没有执行删除。"
    "请重新说明要删除的记忆，或使用 /memories 查看 ID。"
)
_NO_PENDING_CLEAR_ALL_REPLY = "当前没有待确认的长期记忆清空操作。"
_PENDING_CLEAR_ALL_REPLY = (
    "当前正在等待清空长期记忆确认。请回复“确认清空”执行，或回复“取消”。"
)
_CLEAR_ALL_SNAPSHOT_CHANGED_REPLY = (
    "长期记忆列表已发生变化，没有执行清空。请重新发起清空请求。"
)
_PENDING_SELECTION = re.compile(r"(?:#\s*|第\s*)?([0-9]+)(?:\s*条)?\Z")
_GUARDED_FOLLOW_UP = re.compile(r"(?:#\s*[0-9]+|第\s*[0-9]+\s*条)\Z")
_CANCEL_PHRASES = frozenset({"取消", "算了", "不用了"})
_CLEAR_ALL_CONFIRM_PHRASES = frozenset({"确认", "确认清空", "确认全部删除"})
_CLEAR_ALL_CANCEL_PHRASES = frozenset({"取消", "算了", "不要清空"})
_CLEAR_ALL_EXPLICIT_CONFIRM_PHRASES = _CLEAR_ALL_CONFIRM_PHRASES - {"确认"}


@dataclass(frozen=True, slots=True)
class PendingClearAll:
    expected_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class IntentValidationFailure:
    category: str
    safe_reason_enum: str
    safe_field_names: tuple[str, ...]
    error_type: str


@dataclass(frozen=True, slots=True)
class MemoryInteractionResult:
    handled: bool
    reply: str


def _unexpected_arguments_failure(intent: MemoryIntent) -> IntentValidationFailure:
    fields: list[str] = []
    if intent.content is not None:
        fields.append("content")
    if intent.memory_ids:
        fields.append("memory_ids")
    if intent.evidence:
        fields.append("evidence")
    return IntentValidationFailure(
        category="invalid_schema",
        safe_reason_enum="unexpected_arguments",
        safe_field_names=tuple(fields),
        error_type="unexpected_arguments",
    )


class MemoryInteractionCoordinator:
    def __init__(
        self,
        store: MemoryStore,
        router: MemoryIntentRouter,
        *,
        router_profile: ModelProfile | None = None,
    ) -> None:
        self.store = store
        self.router = router
        self.router_profile = router_profile
        self._last_created_memory_id: int | None = None
        self._last_listed_memory_ids: tuple[int, ...] = ()
        self._pending_clarification: PendingClarification | None = None
        self._pending_clear_all: PendingClearAll | None = None
        self._memory_followup_guard = False

    async def process(
        self,
        text: str,
        *,
        recent_user_messages: Sequence[str] = (),
    ) -> MemoryInteractionResult:
        pending_clear_all_at_entry = self._pending_clear_all is not None
        reply = await self.handle(
            text,
            recent_user_messages=recent_user_messages,
        )
        if pending_clear_all_at_entry:
            return MemoryInteractionResult(
                handled=True,
                reply=reply or _PENDING_CLEAR_ALL_REPLY,
            )
        if reply is None:
            return MemoryInteractionResult(handled=False, reply="")
        return MemoryInteractionResult(handled=True, reply=reply)

    async def handle(
        self,
        text: str,
        *,
        recent_user_messages: Sequence[str] = (),
    ) -> str | None:
        pending_clear_reply = self._handle_pending_clear_all(text)
        if pending_clear_reply is not None:
            return pending_clear_reply

        orphan_confirmation_reply = self._handle_orphan_clear_confirmation(text)
        if orphan_confirmation_reply is not None:
            return orphan_confirmation_reply

        deterministic_route = route_memory_command(text)
        if isinstance(deterministic_route, ParsedMemoryCommand):
            self._pending_clarification = None
            self._memory_followup_guard = False
            return self._execute(deterministic_route).reply

        pending_reply = self._handle_pending_locally(text)
        if pending_reply is not None:
            return pending_reply

        guarded_reply = self._handle_guarded_follow_up(text)
        if guarded_reply is not None:
            return guarded_reply

        potential_action: PotentialMemoryAction | None = None
        if not isinstance(deterministic_route, UnsupportedMemoryCommand):
            potential_action = classify_potential_memory_intent(
                text,
                pending_clarification=self._pending_clarification is not None,
            )
        if (
            not isinstance(deterministic_route, UnsupportedMemoryCommand)
            and potential_action is None
        ):
            return None

        telemetry = current_request_telemetry()
        telemetry.mark_memory_command(
            deterministic_route.command
            if isinstance(deterministic_route, UnsupportedMemoryCommand)
            else potential_action or "clarify"
        )
        with telemetry.measure_phase(
            "memory_operation_ms",
            FailurePhase.MEMORY_COMMAND,
        ):
            memories = self.store.list_memories()

        request = MemoryRouterRequest(
            text=text,
            memories=memories,
            recent_user_messages=tuple(recent_user_messages)[-2:],
            last_created_memory_id=self._last_created_memory_id,
            last_listed_memory_ids=self._last_listed_memory_ids,
            pending_clarification=self._pending_clarification,
        )
        router_started_at = telemetry.start_memory_router(
            profile=self.router_profile,
        )
        try:
            routed_intent = await self.router.route(request)
        except asyncio.CancelledError:
            telemetry.finish_memory_router(router_started_at, action="error")
            telemetry.mark_failure(FailurePhase.MEMORY_ROUTER)
            raise
        except MemoryRouterError as error:
            telemetry.finish_memory_router(router_started_at, action="invalid")
            self._record_router_failure(
                stage=error.stage,
                category=error.category,
                safe_reason_enum=error.safe_reason_enum,
                safe_field_names=error.safe_field_names,
                error_type=error.error_type,
            )
            return MEMORY_ROUTER_FAILURE_REPLY
        except LLMError as error:
            action = (
                "invalid"
                if error.code in {"llm_empty_response", "llm_truncated_response"}
                else "error"
            )
            telemetry.finish_memory_router(router_started_at, action=action)
            self._record_router_failure(
                stage="provider",
                category="provider_error",
                safe_reason_enum="provider_failure",
                safe_field_names=(),
                error_type=error.error_type,
            )
            return MEMORY_ROUTER_FAILURE_REPLY
        except Exception as error:  # noqa: BLE001 - fail closed at trust boundary
            telemetry.finish_memory_router(router_started_at, action="error")
            self._record_router_failure(
                stage="provider",
                category="provider_error",
                safe_reason_enum="provider_failure",
                safe_field_names=(),
                error_type=type(error).__name__,
            )
            return MEMORY_ROUTER_FAILURE_REPLY

        validation_failure = self._validate_intent(routed_intent)
        if validation_failure is not None:
            telemetry.finish_memory_router(router_started_at, action="invalid")
            self._record_router_failure(
                stage="core_validation",
                category=validation_failure.category,
                safe_reason_enum=validation_failure.safe_reason_enum,
                safe_field_names=validation_failure.safe_field_names,
                error_type=validation_failure.error_type,
            )
            return MEMORY_ROUTER_FAILURE_REPLY

        telemetry.finish_memory_router(
            router_started_at,
            action=routed_intent.action,
        )
        self._memory_followup_guard = False

        if routed_intent.action == "chat":
            self._pending_clarification = None
            if isinstance(deterministic_route, UnsupportedMemoryCommand):
                return execute_memory_command_route_result(
                    deterministic_route,
                    self.store,
                ).reply
            if potential_action == "forget":
                self._memory_followup_guard = True
            return MEMORY_ROUTER_FAILURE_REPLY
        if routed_intent.action == "remember":
            return self._handle_remember(
                routed_intent,
                text=text,
                recent_user_messages=recent_user_messages,
            )
        if routed_intent.action == "list":
            self._pending_clarification = None
            return self._execute(
                ParsedMemoryCommand(command="memories", argument=None)
            ).reply
        if routed_intent.action == "forget":
            return self._handle_forget(routed_intent)
        if routed_intent.action == "clear_all":
            return self._handle_clear_all(memories)
        return self._handle_clarify(routed_intent)

    def _handle_pending_clear_all(self, text: str) -> str | None:
        pending = self._pending_clear_all
        if pending is None:
            return None

        telemetry = current_request_telemetry()
        telemetry.mark_memory_command("clear_all")
        normalized = text.strip().rstrip("。.!！?？").strip()
        if normalized in _CLEAR_ALL_CANCEL_PHRASES:
            self._pending_clear_all = None
            return _CANCEL_REPLY
        if normalized not in _CLEAR_ALL_CONFIRM_PHRASES:
            return _PENDING_CLEAR_ALL_REPLY

        self._pending_clear_all = None
        with telemetry.measure_phase(
            "memory_operation_ms",
            FailurePhase.MEMORY_COMMAND,
        ):
            result = self.store.clear_all(pending.expected_ids)
        if result.status == "snapshot_changed":
            return _CLEAR_ALL_SNAPSHOT_CHANGED_REPLY

        self._clear_recent_memory_context()
        return f"已清空 {result.cleared_count} 条长期记忆。"

    def _handle_orphan_clear_confirmation(self, text: str) -> str | None:
        normalized = text.strip().rstrip("。.!！?？").strip()
        if normalized not in _CLEAR_ALL_EXPLICIT_CONFIRM_PHRASES:
            return None
        current_request_telemetry().mark_memory_command("clear_all")
        return _NO_PENDING_CLEAR_ALL_REPLY

    def _handle_pending_locally(self, text: str) -> str | None:
        pending = self._pending_clarification
        if pending is None:
            return None
        normalized = text.strip().rstrip("。.!！?？").strip()
        if normalized in _CANCEL_PHRASES:
            self._pending_clarification = None
            return _CANCEL_REPLY
        match = _PENDING_SELECTION.fullmatch(normalized)
        if match is None:
            return None
        memory_id = int(match.group(1))
        if memory_id not in pending.candidate_ids:
            current_request_telemetry().mark_memory_command("clarify")
            return _INVALID_PENDING_SELECTION_REPLY
        self._pending_clarification = None
        return self._execute(
            ParsedMemoryCommand(command="forget", argument=str(memory_id))
        ).reply

    def _handle_guarded_follow_up(self, text: str) -> str | None:
        if not self._memory_followup_guard:
            return None
        self._memory_followup_guard = False
        normalized = text.strip().rstrip("。.!！?？").strip()
        if _GUARDED_FOLLOW_UP.fullmatch(normalized) is None:
            return None
        current_request_telemetry().mark_memory_command("clarify")
        return _NO_PENDING_SELECTION_REPLY

    @staticmethod
    def _validate_intent(intent: MemoryIntent) -> IntentValidationFailure | None:
        if any(memory_id < 1 for memory_id in intent.memory_ids):
            return IntentValidationFailure(
                category="id_validation",
                safe_reason_enum="invalid_id_value",
                safe_field_names=("memory_ids",),
                error_type="non_positive_id",
            )
        if len(set(intent.memory_ids)) != len(intent.memory_ids):
            return IntentValidationFailure(
                category="id_validation",
                safe_reason_enum="duplicate_id",
                safe_field_names=("memory_ids",),
                error_type="duplicate_id",
            )
        if any(not evidence for evidence in intent.evidence):
            return IntentValidationFailure(
                category="evidence_validation",
                safe_reason_enum="unverified_evidence",
                safe_field_names=("evidence",),
                error_type="empty_evidence",
            )
        if intent.action in {"chat", "list"}:
            if intent.content is not None or intent.memory_ids or intent.evidence:
                return _unexpected_arguments_failure(intent)
        elif intent.action == "remember":
            if intent.content is None or not intent.content.strip():
                return IntentValidationFailure(
                    category="invalid_schema",
                    safe_reason_enum="missing_required_field",
                    safe_field_names=("content",),
                    error_type="missing_content",
                )
            if intent.memory_ids:
                return IntentValidationFailure(
                    category="id_validation",
                    safe_reason_enum="unexpected_arguments",
                    safe_field_names=("memory_ids",),
                    error_type="unexpected_id",
                )
        elif intent.action == "forget":
            if intent.content is not None or intent.evidence:
                return _unexpected_arguments_failure(intent)
            if not intent.memory_ids:
                return IntentValidationFailure(
                    category="id_validation",
                    safe_reason_enum="missing_required_field",
                    safe_field_names=("memory_ids",),
                    error_type="missing_id",
                )
        elif intent.action == "clear_all":
            if intent.content is not None or intent.memory_ids or intent.evidence:
                return _unexpected_arguments_failure(intent)
        elif intent.content is not None or intent.evidence:
            return _unexpected_arguments_failure(intent)
        return None

    def _handle_remember(
        self,
        intent: MemoryIntent,
        *,
        text: str,
        recent_user_messages: Sequence[str],
    ) -> str:
        assert intent.content is not None
        sources = (text, *tuple(recent_user_messages)[-2:])
        content_has_exact_source = any(intent.content in source for source in sources)
        evidence_is_verified = bool(intent.evidence) and all(
            any(evidence in source for source in sources) for evidence in intent.evidence
        )
        if not content_has_exact_source and not evidence_is_verified:
            self._last_created_memory_id = None
            self._pending_clarification = None
            self._record_core_validation_failure(
                category="evidence_validation",
                safe_reason_enum="unverified_evidence",
                safe_field_names=("content", "evidence"),
                error_type="unverified_source",
                arm_followup_guard=False,
            )
            return _UNVERIFIED_REMEMBER_REPLY
        self._pending_clarification = None
        return self._execute(
            ParsedMemoryCommand(command="remember", argument=intent.content)
        ).reply

    def _handle_forget(self, intent: MemoryIntent) -> str:
        memories = self.store.list_memories()
        existing_ids = {memory.id for memory in memories}
        if any(memory_id not in existing_ids for memory_id in intent.memory_ids):
            self._pending_clarification = None
            self._record_core_validation_failure(
                category="id_validation",
                safe_reason_enum="unknown_memory_id",
                safe_field_names=("memory_ids",),
                error_type="unknown_id",
            )
            return _UNRESOLVED_FORGET_REPLY
        if len(intent.memory_ids) != 1:
            return self._set_forget_clarification(intent.memory_ids, memories)
        self._pending_clarification = None
        return self._execute(
            ParsedMemoryCommand(
                command="forget",
                argument=str(intent.memory_ids[0]),
            )
        ).reply

    def _handle_clarify(self, intent: MemoryIntent) -> str:
        memories = self.store.list_memories()
        existing_ids = {memory.id for memory in memories}
        candidates = tuple(
            memory_id
            for memory_id in intent.memory_ids
            if memory_id in existing_ids
        )
        if not candidates:
            self._pending_clarification = None
            return _GENERIC_CLARIFICATION_REPLY
        return self._set_forget_clarification(candidates, memories)

    def _handle_clear_all(self, memories: tuple[PinnedMemory, ...]) -> str:
        self._pending_clarification = None
        self._memory_followup_guard = False
        if not memories:
            self._pending_clear_all = None
            return "当前没有已保存的长期记忆，无需清空。"
        expected_ids = tuple(memory.id for memory in memories)
        self._pending_clear_all = PendingClearAll(expected_ids=expected_ids)
        return (
            f"这会删除当前全部 {len(expected_ids)} 条长期记忆。"
            "请回复“确认清空”执行，或回复“取消”。"
        )

    def _record_router_failure(
        self,
        *,
        stage: str,
        category: str,
        safe_reason_enum: str,
        safe_field_names: tuple[str, ...],
        error_type: str,
        arm_followup_guard: bool = True,
    ) -> None:
        if arm_followup_guard and self._pending_clarification is None:
            self._memory_followup_guard = True
        telemetry = current_request_telemetry()
        safe_fields = ",".join(safe_field_names) or "none"
        logger.warning(
            "Memory router failed request_id=%s stage=%s category=%s "
            "reason=%s fields=%s error_type=%s",
            telemetry.request_id,
            stage,
            category,
            safe_reason_enum,
            safe_fields,
            error_type,
        )

    def _record_core_validation_failure(
        self,
        *,
        category: str,
        safe_reason_enum: str,
        safe_field_names: tuple[str, ...],
        error_type: str,
        arm_followup_guard: bool = True,
    ) -> None:
        self._record_router_failure(
            stage="core_validation",
            category=category,
            safe_reason_enum=safe_reason_enum,
            safe_field_names=safe_field_names,
            error_type=error_type,
            arm_followup_guard=arm_followup_guard,
        )

    def _set_forget_clarification(
        self,
        candidate_ids: Sequence[int],
        memories: tuple[PinnedMemory, ...],
    ) -> str:
        unique_candidates = tuple(dict.fromkeys(candidate_ids))
        memory_by_id = {memory.id: memory for memory in memories}
        valid_candidates = tuple(
            memory_id
            for memory_id in unique_candidates
            if memory_id in memory_by_id
        )
        if not valid_candidates:
            self._pending_clarification = None
            return _UNRESOLVED_FORGET_REPLY
        self._pending_clarification = PendingClarification(
            action="forget",
            candidate_ids=valid_candidates,
        )
        records = "\n".join(
            f"#{memory_id} {memory_by_id[memory_id].content}"
            for memory_id in valid_candidates
        )
        return (
            "找到多条可能的长期记忆，请回复 #<id> 指定要删除的记录：\n"
            + records
        )

    def _execute(self, command: ParsedMemoryCommand) -> MemoryExecutionResult:
        telemetry = current_request_telemetry()
        telemetry.mark_memory_command(command.command)
        with telemetry.measure_phase(
            "memory_operation_ms",
            FailurePhase.MEMORY_COMMAND,
        ):
            result = execute_parsed_memory_command_result(command, self.store)
        self._update_recent_context(result)
        return result

    def _update_recent_context(self, result: MemoryExecutionResult) -> None:
        if result.command == "remember":
            if result.outcome == "created":
                self._last_created_memory_id = result.memory_ids[0]
            else:
                self._last_created_memory_id = None
        elif result.command == "memories" and result.outcome in {"listed", "empty"}:
            self._last_listed_memory_ids = result.memory_ids
        elif result.command == "forget" and result.outcome == "deleted":
            deleted_ids = set(result.memory_ids)
            if self._last_created_memory_id in deleted_ids:
                self._last_created_memory_id = None
            self._last_listed_memory_ids = tuple(
                memory_id
                for memory_id in self._last_listed_memory_ids
                if memory_id not in deleted_ids
            )

    def _clear_recent_memory_context(self) -> None:
        self._last_created_memory_id = None
        self._last_listed_memory_ids = ()
        self._pending_clarification = None
        self._memory_followup_guard = False
