import importlib
import json
from pathlib import Path

import pytest

from jarvis_core.memory_store import SQLiteMemoryStore


def memory_commands_module():  # type: ignore[no-untyped-def]
    return importlib.import_module("jarvis_core.memory_commands")


def make_store(tmp_path: Path, *, max_memories: int = 20) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(
        tmp_path / "memory.db",
        max_memories=max_memories,
    )


@pytest.mark.parametrize(
    ("command", "expected_content"),
    [
        ("/remember keep this", "keep this"),
        ("/REMEMBER keep this", "keep this"),
        ("  /Remember   keep  internal\nspacing  ", "keep  internal\nspacing"),
    ],
)
def test_remember_command_is_case_insensitive_and_preserves_content(
    tmp_path: Path,
    command: str,
    expected_content: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)

    reply = module.execute_memory_command(command, store)

    assert reply == "已保存长期记忆 #1。"
    assert store.list_memories()[0].content == expected_content


def test_duplicate_remember_returns_original_id(tmp_path: Path) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)

    assert module.execute_memory_command("/remember alpha", store) == (
        "已保存长期记忆 #1。"
    )
    assert module.execute_memory_command("/REMEMBER  alpha  ", store) == (
        "该长期记忆已存在（#1）。"
    )


def test_structured_execution_result_comes_from_store_outcome(tmp_path: Path) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    parsed = module.ParsedMemoryCommand(command="remember", argument="alpha")

    created = module.execute_parsed_memory_command_result(parsed, store)
    duplicate = module.execute_parsed_memory_command_result(parsed, store)

    assert created == module.MemoryExecutionResult(
        command="remember",
        outcome="created",
        memory_ids=(1,),
        reply="已保存长期记忆 #1。",
    )
    assert duplicate == module.MemoryExecutionResult(
        command="remember",
        outcome="duplicate",
        memory_ids=(1,),
        reply="该长期记忆已存在（#1）。",
    )


def test_structured_list_and_forget_results_report_real_ids(tmp_path: Path) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("one")
    store.remember("two")

    listed = module.execute_parsed_memory_command_result(
        module.ParsedMemoryCommand(command="memories", argument=None),
        store,
    )
    deleted = module.execute_parsed_memory_command_result(
        module.ParsedMemoryCommand(command="forget", argument="2"),
        store,
    )
    missing = module.execute_parsed_memory_command_result(
        module.ParsedMemoryCommand(command="forget", argument="9"),
        store,
    )

    assert listed.command == "memories"
    assert listed.outcome == "listed"
    assert listed.memory_ids == (1, 2)
    assert deleted.outcome == "deleted"
    assert deleted.memory_ids == (2,)
    assert missing.outcome == "not_found"
    assert missing.memory_ids == (9,)


def test_structured_invalid_and_limit_results_have_no_success_outcome(
    tmp_path: Path,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path, max_memories=1)
    store.remember("one")

    invalid = module.execute_parsed_memory_command_result(
        module.ParsedMemoryCommand(command="forget", argument="0"),
        store,
    )
    limited = module.execute_parsed_memory_command_result(
        module.ParsedMemoryCommand(command="remember", argument="two"),
        store,
    )

    assert invalid.outcome == "invalid"
    assert invalid.memory_ids == ()
    assert limited.outcome == "limit_reached"
    assert limited.memory_ids == ()
    assert store.list_memories()[0].content == "one"


@pytest.mark.parametrize(
    "command",
    ["/remember", "/remember   ", "/remember\t\n"],
)
def test_empty_remember_returns_fixed_usage(tmp_path: Path, command: str) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)

    reply = module.execute_memory_command(command, store)

    assert reply == "用法：/remember <content>"
    assert store.list_memories() == ()


def test_overlong_remember_returns_fixed_limit_message(tmp_path: Path) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)

    reply = module.execute_memory_command("/remember " + "x" * 501, store)

    assert reply == "长期记忆内容不能超过 500 个字符。"
    assert store.list_memories() == ()


def test_full_store_returns_fixed_capacity_message(tmp_path: Path) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path, max_memories=1)
    module.execute_memory_command("/remember one", store)

    reply = module.execute_memory_command("/remember two", store)

    assert reply == "长期记忆已达到 1 条上限，请先使用 /forget <id> 删除一条。"


@pytest.mark.parametrize("command", ["/memories", "/MEMORIES", " /Memories "])
def test_empty_memories_command_is_case_insensitive(
    tmp_path: Path,
    command: str,
) -> None:
    module = memory_commands_module()

    reply = module.execute_memory_command(command, make_store(tmp_path))

    assert reply == "当前没有已保存的长期记忆。"


def test_memories_uses_json_escaping_and_id_order(tmp_path: Path) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    first_content = 'line one\n"quoted" \\ slash'
    module.execute_memory_command(f"/remember {first_content}", store)
    module.execute_memory_command("/remember second", store)

    reply = module.execute_memory_command("/memories", store)

    assert reply.startswith("已保存的长期记忆：\n")
    records = json.loads(reply.removeprefix("已保存的长期记忆：\n"))
    assert records == [
        {"id": 1, "content": first_content},
        {"id": 2, "content": "second"},
    ]
    assert "\\n" in reply
    assert '\\"quoted\\"' in reply


def test_memories_with_arguments_returns_fixed_usage(tmp_path: Path) -> None:
    module = memory_commands_module()

    reply = module.execute_memory_command("/memories extra", make_store(tmp_path))

    assert reply == "用法：/memories"


@pytest.mark.parametrize("command", ["/forget 1", "/FORGET 1", " /Forget 1 "])
def test_forget_command_is_case_insensitive(tmp_path: Path, command: str) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("saved")

    reply = module.execute_memory_command(command, store)

    assert reply == "已删除长期记忆 #1。"
    assert store.list_memories() == ()


def test_forget_missing_id_returns_fixed_reply(tmp_path: Path) -> None:
    module = memory_commands_module()

    reply = module.execute_memory_command("/forget 8", make_store(tmp_path))

    assert reply == "没有找到长期记忆 #8。"


@pytest.mark.parametrize(
    "command",
    [
        "/forget",
        "/forget 0",
        "/forget -1",
        "/forget 1.0",
        "/forget +1",
        "/forget １",
        "/forget 1 extra",
    ],
)
def test_forget_rejects_non_positive_ascii_decimal_ids(
    tmp_path: Path,
    command: str,
) -> None:
    module = memory_commands_module()

    reply = module.execute_memory_command(command, make_store(tmp_path))

    assert reply == "用法：/forget <positive-id>"


@pytest.mark.parametrize(
    "message",
    ["ordinary text", "/unknown argument", "/remembering something"],
)
def test_non_memory_commands_are_not_consumed(tmp_path: Path, message: str) -> None:
    module = memory_commands_module()

    reply = module.execute_memory_command(message, make_store(tmp_path))

    assert reply is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/remember private value", "remember"),
        (" /REMEMBER ", "remember"),
        ("/memories", "memories"),
        ("/Memories unexpected argument", "memories"),
        ("/forget 7", "forget"),
        ("/FORGET not-an-id", "forget"),
        ("ordinary text", None),
        ("/unknown private value", None),
    ],
)
def test_identify_memory_command_returns_only_safe_command_name(
    text: str,
    expected: str | None,
) -> None:
    module = memory_commands_module()

    assert module.identify_memory_command(text) == expected


def test_slash_command_parses_to_unified_representation() -> None:
    module = memory_commands_module()

    parsed = module.parse_memory_command(" /Remember   keep  internal spacing ")

    assert parsed == module.ParsedMemoryCommand(
        command="remember",
        argument="keep  internal spacing",
    )


@pytest.mark.parametrize(
    ("text", "expected_content"),
    [
        ("记住，alpha", "alpha"),
        ("记住：alpha", "alpha"),
        ("请记住，alpha", "alpha"),
        ("帮我记住，alpha", "alpha"),
        ("帮我记一下，alpha", "alpha"),
        ("以后记得，alpha", "alpha"),
        ("记住  ,：；.!。！  alpha", "alpha"),
        ("帮我记一下，keep  internal\nspacing", "keep  internal\nspacing"),
        ('记住，"C++ #topic"', '"C++ #topic"'),
    ],
)
def test_natural_remember_prefixes_parse_without_changing_content(
    text: str,
    expected_content: str,
) -> None:
    module = memory_commands_module()

    parsed = module.parse_memory_command(text)

    assert parsed == module.ParsedMemoryCommand(
        command="remember",
        argument=expected_content,
    )


@pytest.mark.parametrize(
    "text",
    ["记住", "记住，  ", "请记住：", "帮我记一下！"],
)
def test_empty_natural_remember_is_an_explicit_command_with_no_argument(
    text: str,
) -> None:
    module = memory_commands_module()

    parsed = module.parse_memory_command(text)

    assert parsed == module.ParsedMemoryCommand(
        command="remember",
        argument=None,
    )


@pytest.mark.parametrize(
    "text",
    ["记住", "记住，  ", "请记住：", "帮我记一下！"],
)
def test_empty_natural_remember_returns_existing_usage(
    tmp_path: Path,
    text: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)

    reply = module.execute_memory_command(text, store)

    assert reply == "用法：/remember <content>"
    assert store.list_memories() == ()


def test_natural_remember_reuses_duplicate_length_and_capacity_behavior(
    tmp_path: Path,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path, max_memories=2)

    assert module.execute_memory_command("记住，alpha", store) == (
        "已保存长期记忆 #1。"
    )
    assert module.execute_memory_command("请记住：alpha", store) == (
        "该长期记忆已存在（#1）。"
    )
    assert module.execute_memory_command("帮我记住，" + "x" * 501, store) == (
        "长期记忆内容不能超过 500 个字符。"
    )
    assert module.execute_memory_command("以后记得，beta", store) == (
        "已保存长期记忆 #2。"
    )
    assert module.execute_memory_command("记住，gamma", store) == (
        "长期记忆已达到 2 条上限，请先使用 /forget <id> 删除一条。"
    )


@pytest.mark.parametrize(
    "text",
    [
        "你长期记住了什么？",
        "你长期记住了什么?",
        "你记住了哪些长期记忆？",
        "查看长期记忆",
        " 查看长期记忆。 ",
    ],
)
def test_natural_list_commands_reuse_memories_behavior(
    tmp_path: Path,
    text: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("saved value")

    reply = module.execute_memory_command(text, store)

    assert reply is not None
    records = json.loads(reply.removeprefix("已保存的长期记忆：\n"))
    assert records == [{"id": 1, "content": "saved value"}]
    assert module.identify_memory_command(text) == "memories"


@pytest.mark.parametrize(
    "text",
    [
        "忘掉长期记忆 #1",
        "删除长期记忆 # 1。",
        "忘记第1条长期记忆",
        "忘记第 1 条长期记忆！",
    ],
)
def test_natural_forget_commands_delete_only_the_explicit_id(
    tmp_path: Path,
    text: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("saved value")

    reply = module.execute_memory_command(text, store)

    assert reply == "已删除长期记忆 #1。"
    assert store.list_memories() == ()


@pytest.mark.parametrize(
    "text",
    [
        "删除记忆1",
        "删除记忆 #1",
        "删除第1条记忆",
        "忘掉记忆1",
        "忘掉第1条记忆",
        "请删除记忆1",
        "帮我删除记忆1",
    ],
)
def test_short_natural_forget_fast_path_deletes_only_the_explicit_id(
    tmp_path: Path,
    text: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("saved value")

    parsed = module.parse_memory_command(text)
    reply = module.execute_memory_command(text, store)

    assert parsed == module.ParsedMemoryCommand(command="forget", argument="1")
    assert reply == "已删除长期记忆 #1。"
    assert store.list_memories() == ()


def test_short_natural_forget_never_claims_success_without_store_success(
    tmp_path: Path,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("must remain")

    reply = module.execute_memory_command("删除记忆2", store)

    assert reply == "没有找到长期记忆 #2。"
    assert store.list_memories()[0].content == "must remain"


@pytest.mark.parametrize(
    "text",
    ["删除记忆0", "删除记忆-1", "删除记忆+3", "删除记忆abc"],
)
def test_short_natural_forget_invalid_id_stays_local_without_deleting(
    tmp_path: Path,
    text: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("must remain")

    reply = module.execute_memory_command(text, store)

    assert reply == "用法：/forget <positive-id>"
    assert store.list_memories()[0].content == "must remain"


@pytest.mark.parametrize(
    "text",
    [
        "忘掉长期记忆 #0",
        "忘掉长期记忆 #-1",
        "删除长期记忆 #+3",
        "忘记第abc条长期记忆",
    ],
)
def test_invalid_natural_forget_id_returns_usage_without_deleting(
    tmp_path: Path,
    text: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("must remain")

    reply = module.execute_memory_command(text, store)

    assert reply == "用法：/forget <positive-id>"
    assert store.list_memories()[0].content == "must remain"


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("删除记忆", "forget"),
        ("删除这条记忆", "forget"),
        ("删除记忆1和2", "forget"),
        ("删除关于 ROS2 的记忆", "forget"),
        ("把刚才那条长期记忆删掉", "forget"),
        ("忘掉长期记忆 #", "forget"),
        ("忘掉长期记忆 #3 extra", "forget"),
        ("记住我最近在学 ROS2", "remember"),
        ("请查看长期记忆", "memories"),
        ("列出长期记忆", "memories"),
    ],
)
def test_unsupported_explicit_memory_intent_is_routed_locally(
    text: str,
    command: str,
) -> None:
    module = memory_commands_module()

    route = module.route_memory_command(text)

    assert route == module.UnsupportedMemoryCommand(command=command)
    assert module.parse_memory_command(text) is None


@pytest.mark.parametrize(
    ("text", "expected_reply"),
    [
        (
            "删除关于 ROS2 的记忆",
            (
                "无法确定要删除的长期记忆 ID。请使用“删除记忆1”、"
                "“忘掉长期记忆 #1”或 /forget 1。"
            ),
        ),
        (
            "记住我最近在学 ROS2",
            (
                "无法确定要保存的长期记忆内容。请使用“记住，<内容>”"
                "或 /remember <content>。"
            ),
        ),
        (
            "列出长期记忆",
            "请使用“查看长期记忆”或 /memories。",
        ),
    ],
)
def test_unsupported_explicit_memory_intent_returns_guidance_without_side_effect(
    tmp_path: Path,
    text: str,
    expected_reply: str,
) -> None:
    module = memory_commands_module()
    store = make_store(tmp_path)
    store.remember("must remain")

    reply = module.execute_memory_command(text, store)

    assert reply == expected_reply
    memories = store.list_memories()
    assert len(memories) == 1
    assert memories[0].id == 1
    assert memories[0].content == "must remain"


@pytest.mark.parametrize(
    "text",
    [
        "我最近主要学习 ROS2。",
        "你觉得我应该记住什么？",
        "记住这件事重要吗？",
        "以后记得东西是不是很难？",
        "忘记东西很正常。",
        "查看长期记忆的原理",
        "你觉得长期记忆有什么用？",
        "人为什么会忘记东西？",
        "JARVIS以后应该怎么设计记忆？",
        "删除长期记忆是不是危险操作？",
    ],
)
def test_ordinary_memory_discussion_is_not_consumed(text: str) -> None:
    module = memory_commands_module()

    assert module.parse_memory_command(text) is None
    assert module.route_memory_command(text) is None
