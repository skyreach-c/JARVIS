from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from jarvis_core.memory_store import (
    MAX_PINNED_MEMORY_CHARS,
    MemoryLimitReachedError,
    MemoryStore,
    normalize_memory_content,
)

_POSITIVE_DECIMAL_ID = re.compile(r"[0-9]+\Z")
type MemoryCommandName = Literal["remember", "memories", "forget"]
type MemoryCommandOutcome = Literal[
    "created",
    "duplicate",
    "listed",
    "empty",
    "deleted",
    "not_found",
    "invalid",
    "limit_reached",
]

_NATURAL_REMEMBER_PREFIXES = (
    "帮我记一下",
    "帮我记住",
    "以后记得",
    "请记住",
    "记住",
)
_NATURAL_REMEMBER_SEPARATORS = " \t\r\n,，:：;；.!。！"
_NATURAL_LIST_PHRASES = frozenset(
    {
        "你长期记住了什么",
        "你记住了哪些长期记忆",
        "查看长期记忆",
    }
)
_NATURAL_LIST_ENDINGS = "？?。"
_NATURAL_FORGET_HASH = re.compile(
    r"(?:忘掉|删除)长期记忆\s*#\s*"
    r"(?P<memory_id>[^\s。.!！?？]+)\s*[。.!！?？]?\Z"
)
_NATURAL_FORGET_ORDINAL = re.compile(
    r"忘记第\s*(?P<memory_id>[^\s条。.!！?？]+)\s*"
    r"条长期记忆\s*[。.!！?？]?\Z"
)
_SHORT_FORGET_ID_TOKEN = r"[+\-]?[0-9A-Za-z]+"
_NATURAL_FORGET_SHORT = re.compile(
    rf"(?:(?:请|帮我)删除|删除|忘掉)记忆\s*#?\s*"
    rf"(?P<memory_id>{_SHORT_FORGET_ID_TOKEN})\s*[。.!！?？]?\Z"
)
_NATURAL_FORGET_SHORT_ORDINAL = re.compile(
    rf"(?:(?:请|帮我)删除|删除|忘掉)第\s*"
    rf"(?P<memory_id>{_SHORT_FORGET_ID_TOKEN})\s*"
    r"条记忆\s*[。.!！?？]?\Z"
)
_MEMORY_DISCUSSION_MARKERS = (
    "是不是",
    "为什么",
    "怎么",
    "如何",
    "有什么",
    "的原理",
    "重要吗",
    "难吗",
    "危险操作",
)
_FORGET_INTENT_PREFIXES = (
    "删除",
    "请删除",
    "帮我删除",
    "忘掉",
    "忘记",
)
_LIST_INTENT_PREFIXES = ("查看", "请查看", "列出")

_UNSUPPORTED_MEMORY_COMMAND_REPLIES: dict[MemoryCommandName, str] = {
    "remember": (
        "无法确定要保存的长期记忆内容。"
        "请使用“记住，<内容>”或 /remember <content>。"
    ),
    "memories": "请使用“查看长期记忆”或 /memories。",
    "forget": (
        "无法确定要删除的长期记忆 ID。"
        "请使用“删除记忆1”、“忘掉长期记忆 #1”或 /forget 1。"
    ),
}


@dataclass(frozen=True, slots=True)
class ParsedMemoryCommand:
    command: MemoryCommandName
    argument: str | None


@dataclass(frozen=True, slots=True)
class UnsupportedMemoryCommand:
    command: MemoryCommandName


type MemoryCommandRoute = ParsedMemoryCommand | UnsupportedMemoryCommand


@dataclass(frozen=True, slots=True)
class MemoryExecutionResult:
    command: MemoryCommandName
    outcome: MemoryCommandOutcome
    memory_ids: tuple[int, ...]
    reply: str


def parse_memory_command(text: str) -> ParsedMemoryCommand | None:
    stripped_text = text.strip()
    if not stripped_text:
        return None

    slash_command = _parse_slash_command(stripped_text)
    if slash_command is not None:
        return slash_command

    natural_remember = _parse_natural_remember(stripped_text)
    if natural_remember is not None:
        return natural_remember

    list_phrase = stripped_text
    if list_phrase[-1] in _NATURAL_LIST_ENDINGS:
        list_phrase = list_phrase[:-1].rstrip()
    if list_phrase in _NATURAL_LIST_PHRASES:
        return ParsedMemoryCommand(command="memories", argument=None)

    for pattern in (
        _NATURAL_FORGET_HASH,
        _NATURAL_FORGET_ORDINAL,
        _NATURAL_FORGET_SHORT,
        _NATURAL_FORGET_SHORT_ORDINAL,
    ):
        match = pattern.fullmatch(stripped_text)
        if match is not None:
            return ParsedMemoryCommand(
                command="forget",
                argument=match.group("memory_id"),
            )
    return None


def route_memory_command(text: str) -> MemoryCommandRoute | None:
    parsed = parse_memory_command(text)
    if parsed is not None:
        return parsed

    command = _identify_unsupported_memory_intent(text.strip())
    if command is None:
        return None
    return UnsupportedMemoryCommand(command=command)


def _identify_unsupported_memory_intent(
    stripped_text: str,
) -> MemoryCommandName | None:
    if not stripped_text or _is_memory_discussion(stripped_text):
        return None

    if _looks_like_unsupported_forget(stripped_text):
        return "forget"
    if any(stripped_text.startswith(prefix) for prefix in _NATURAL_REMEMBER_PREFIXES):
        return "remember"
    if (
        "长期记忆" in stripped_text
        and stripped_text.startswith(_LIST_INTENT_PREFIXES)
    ):
        return "memories"
    return None


def _is_memory_discussion(stripped_text: str) -> bool:
    return any(marker in stripped_text for marker in _MEMORY_DISCUSSION_MARKERS)


def _looks_like_unsupported_forget(stripped_text: str) -> bool:
    if "记忆" not in stripped_text:
        return False
    if stripped_text.startswith(_FORGET_INTENT_PREFIXES):
        return True
    return stripped_text.startswith("把") and re.search(
        r"(?:删除|删掉|忘掉|忘记)\s*[。.!！?？]?\Z",
        stripped_text,
    ) is not None


def _parse_slash_command(stripped_text: str) -> ParsedMemoryCommand | None:
    parts = stripped_text.split(maxsplit=1)
    command_token = parts[0].casefold()
    argument = parts[1] if len(parts) == 2 else None
    if command_token == "/remember":
        return ParsedMemoryCommand(command="remember", argument=argument)
    if command_token == "/memories":
        return ParsedMemoryCommand(command="memories", argument=argument)
    if command_token == "/forget":
        return ParsedMemoryCommand(command="forget", argument=argument)
    return None


def _parse_natural_remember(
    stripped_text: str,
) -> ParsedMemoryCommand | None:
    for prefix in _NATURAL_REMEMBER_PREFIXES:
        if stripped_text == prefix:
            return ParsedMemoryCommand(command="remember", argument=None)
        if not stripped_text.startswith(prefix):
            continue

        remainder = stripped_text[len(prefix) :]
        if not remainder or remainder[0] not in _NATURAL_REMEMBER_SEPARATORS:
            continue
        argument = remainder.lstrip(_NATURAL_REMEMBER_SEPARATORS) or None
        return ParsedMemoryCommand(command="remember", argument=argument)
    return None


def identify_memory_command(text: str) -> MemoryCommandName | None:
    route = route_memory_command(text)
    return route.command if route is not None else None


def execute_memory_command(text: str, store: MemoryStore) -> str | None:
    route = route_memory_command(text)
    if route is None:
        return None
    return execute_memory_command_route(route, store)


def execute_memory_command_route(
    route: MemoryCommandRoute,
    store: MemoryStore,
) -> str:
    return execute_memory_command_route_result(route, store).reply


def execute_memory_command_route_result(
    route: MemoryCommandRoute,
    store: MemoryStore,
) -> MemoryExecutionResult:
    if isinstance(route, UnsupportedMemoryCommand):
        return MemoryExecutionResult(
            command=route.command,
            outcome="invalid",
            memory_ids=(),
            reply=_UNSUPPORTED_MEMORY_COMMAND_REPLIES[route.command],
        )
    return execute_parsed_memory_command_result(route, store)


def execute_parsed_memory_command(
    parsed: ParsedMemoryCommand,
    store: MemoryStore,
) -> str:
    return execute_parsed_memory_command_result(parsed, store).reply


def execute_parsed_memory_command_result(
    parsed: ParsedMemoryCommand,
    store: MemoryStore,
) -> MemoryExecutionResult:
    if parsed.command == "remember":
        return _remember_result(parsed.argument, store)
    if parsed.command == "memories":
        return _list_memories_result(parsed.argument, store)
    return _forget_result(parsed.argument, store)


def _remember_result(
    argument: str | None,
    store: MemoryStore,
) -> MemoryExecutionResult:
    if argument is None:
        return MemoryExecutionResult(
            command="remember",
            outcome="invalid",
            memory_ids=(),
            reply="用法：/remember <content>",
        )
    try:
        normalized_content = normalize_memory_content(argument)
    except ValueError:
        if len(argument.strip()) > MAX_PINNED_MEMORY_CHARS:
            reply = f"长期记忆内容不能超过 {MAX_PINNED_MEMORY_CHARS} 个字符。"
        else:
            reply = "用法：/remember <content>"
        return MemoryExecutionResult(
            command="remember",
            outcome="invalid",
            memory_ids=(),
            reply=reply,
        )

    try:
        result = store.remember(normalized_content)
    except MemoryLimitReachedError as error:
        return MemoryExecutionResult(
            command="remember",
            outcome="limit_reached",
            memory_ids=(),
            reply=(
                f"长期记忆已达到 {error.limit} 条上限，"
                "请先使用 /forget <id> 删除一条。"
            ),
        )

    if result.created:
        return MemoryExecutionResult(
            command="remember",
            outcome="created",
            memory_ids=(result.memory.id,),
            reply=f"已保存长期记忆 #{result.memory.id}。",
        )
    return MemoryExecutionResult(
        command="remember",
        outcome="duplicate",
        memory_ids=(result.memory.id,),
        reply=f"该长期记忆已存在（#{result.memory.id}）。",
    )


def _list_memories_result(
    argument: str | None,
    store: MemoryStore,
) -> MemoryExecutionResult:
    if argument is not None:
        return MemoryExecutionResult(
            command="memories",
            outcome="invalid",
            memory_ids=(),
            reply="用法：/memories",
        )

    memories = store.list_memories()
    if not memories:
        return MemoryExecutionResult(
            command="memories",
            outcome="empty",
            memory_ids=(),
            reply="当前没有已保存的长期记忆。",
        )
    payload = [
        {"id": memory.id, "content": memory.content} for memory in memories
    ]
    return MemoryExecutionResult(
        command="memories",
        outcome="listed",
        memory_ids=tuple(memory.id for memory in memories),
        reply="已保存的长期记忆：\n"
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def _forget_result(
    argument: str | None,
    store: MemoryStore,
) -> MemoryExecutionResult:
    if argument is None or _POSITIVE_DECIMAL_ID.fullmatch(argument) is None:
        return MemoryExecutionResult(
            command="forget",
            outcome="invalid",
            memory_ids=(),
            reply="用法：/forget <positive-id>",
        )
    memory_id = int(argument)
    if memory_id < 1:
        return MemoryExecutionResult(
            command="forget",
            outcome="invalid",
            memory_ids=(),
            reply="用法：/forget <positive-id>",
        )

    if store.forget(memory_id):
        return MemoryExecutionResult(
            command="forget",
            outcome="deleted",
            memory_ids=(memory_id,),
            reply=f"已删除长期记忆 #{memory_id}。",
        )
    return MemoryExecutionResult(
        command="forget",
        outcome="not_found",
        memory_ids=(memory_id,),
        reply=f"没有找到长期记忆 #{memory_id}。",
    )
