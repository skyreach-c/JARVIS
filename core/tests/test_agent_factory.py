import ast
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from jarvis_core.agent.brain import StructuredAgentDecisionModel
from jarvis_core.agent.factory import create_agent_response_runtime
from jarvis_core.llm.client import ChatMessage
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.telemetry import (
    RequestTelemetry,
    bind_request_telemetry,
    reset_request_telemetry,
)
from jarvis_core.tools import filesystem
from jarvis_core.tools.contracts import ToolCall
from jarvis_core.tools.filesystem import (
    GET_METADATA_TOOL_NAME,
    LIST_DIRECTORY_TOOL_NAME,
)
from jarvis_core.tools.runtime_info import RUNTIME_INFO_TOOL_NAME
from jarvis_core.tools.system_info import OS_INFO_TOOL_NAME


class FakeStructuredClient:
    def __init__(
        self,
        response: str = '{"action":"respond","tool_name":null,"arguments":{}}',
    ) -> None:
        self.response = response
        self.calls: list[tuple[tuple[ChatMessage, ...], int]] = []

    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
    ) -> str:
        self.calls.append(
            (
                tuple(dict(message) for message in messages),  # type: ignore[misc]
                max_tokens,
            )
        )
        return self.response


class FakeChatClient:
    def __init__(self) -> None:
        self.calls: list[tuple[ChatMessage, ...]] = []

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        self.calls.append(
            tuple(dict(message) for message in messages)  # type: ignore[misc]
        )
        yield "answer"


async def test_factory_binds_profiles_chat_and_four_read_only_tools_in_order(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "notes").mkdir()
    project_file = project_root / "notes" / "fact.txt"
    project_file.write_text("content-must-not-be-read", encoding="utf-8")
    brain_client = FakeStructuredClient()
    chat_client = FakeChatClient()
    brain_profile = ModelProfile(
        name="agent_brain",
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    chat_profile = ModelProfile(
        name="reasoning_strong",
        provider="packycode",
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )

    runtime = create_agent_response_runtime(
        brain_client=brain_client,
        brain_profile=brain_profile,
        chat_client=chat_client,
        chat_profile=chat_profile,
        project_root=project_root.resolve(strict=True),
    )

    assert isinstance(runtime.brain, StructuredAgentDecisionModel)
    assert runtime.brain_profile is brain_profile
    assert runtime.chat_client is chat_client
    assert runtime.chat_profile is chat_profile
    definitions = runtime.registry.definitions()
    assert tuple(definition.name for definition in definitions) == (
        RUNTIME_INFO_TOOL_NAME,
        OS_INFO_TOOL_NAME,
        LIST_DIRECTORY_TOOL_NAME,
        GET_METADATA_TOOL_NAME,
    )
    assert {definition.risk_level for definition in definitions} == {"read_only"}

    runtime_result = await runtime.registry.execute(
        ToolCall(tool_name=RUNTIME_INFO_TOOL_NAME, arguments={}),
        request_id="factory-runtime-info",
    )
    os_result = await runtime.registry.execute(
        ToolCall(tool_name=OS_INFO_TOOL_NAME, arguments={}),
        request_id="factory-os-info",
    )
    list_result = await runtime.registry.execute(
        ToolCall(
            tool_name=LIST_DIRECTORY_TOOL_NAME,
            arguments={"relative_path": "notes", "limit": 10},
        ),
        request_id="factory-list-directory",
    )
    metadata_result = await runtime.registry.execute(
        ToolCall(
            tool_name=GET_METADATA_TOOL_NAME,
            arguments={"relative_path": "notes/fact.txt"},
        ),
        request_id="factory-get-metadata",
    )

    assert runtime_result.success is True
    assert runtime_result.data is not None
    assert runtime_result.data["chat_profile"] == "reasoning_strong"
    assert runtime_result.data["provider"] == "packycode"
    assert runtime_result.data["model"] == "gpt-5.6-sol"
    assert os_result.success is True
    assert os_result.data is not None
    assert set(os_result.data) == {
        "os_family",
        "os_release",
        "os_version",
        "architecture",
        "logical_cpu_count",
    }
    assert list_result.success is True
    assert list_result.data is not None
    assert list_result.data["relative_path"] == "notes"
    assert list_result.data["entries"] == [
        {"name": "fact.txt", "kind": "file", "size_bytes": project_file.stat().st_size}
    ]
    assert metadata_result.success is True
    assert metadata_result.data == {
        "scope": "project",
        "relative_path": "notes/fact.txt",
        "exists": True,
        "kind": "file",
        "size_bytes": project_file.stat().st_size,
    }


async def test_factory_metadata_tool_executes_once_and_chat_observes_only_tool_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    content_marker = "PRIVATE-FILE-CONTENT-MUST-NOT-BE-OBSERVED"
    target = project_root / "artifact.txt"
    target.write_text(content_marker, encoding="utf-8")
    user_marker = "PRIVATE-USER-PROMPT-MUST-NOT-ENTER-TELEMETRY"
    brain_client = FakeStructuredClient(
        '{"action":"call_tool","tool_name":"filesystem.get_metadata",'
        '"arguments":{"relative_path":"artifact.txt"}}'
    )
    chat_client = FakeChatClient()
    brain_profile = ModelProfile(
        name="agent_brain",
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    chat_profile = ModelProfile(
        name="reasoning_strong",
        provider="packycode",
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    real_execute = filesystem.GetMetadataExecutor.execute
    executor_results = []

    async def counted_execute(self, arguments):  # type: ignore[no-untyped-def]
        result = await real_execute(self, arguments)
        executor_results.append(result)
        return result

    monkeypatch.setattr(filesystem.GetMetadataExecutor, "execute", counted_execute)
    runtime = create_agent_response_runtime(
        brain_client=brain_client,
        brain_profile=brain_profile,
        chat_client=chat_client,
        chat_profile=chat_profile,
        project_root=project_root.resolve(strict=True),
    )
    upstream_messages: list[ChatMessage] = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": user_marker},
    ]
    upstream_snapshot = tuple(dict(message) for message in upstream_messages)
    summaries: list[dict[str, object]] = []
    telemetry = RequestTelemetry(
        "factory-metadata-integration",
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=0)
    token = bind_request_telemetry(telemetry)
    try:
        chunks = [
            chunk
            async for chunk in runtime.stream_response(
                upstream_messages,
                current_user_message=user_marker,
                request_id="factory-metadata-integration",
            )
        ]
        telemetry.finish(status="success")
    finally:
        reset_request_telemetry(token)

    assert chunks == ["answer"]
    assert len(brain_client.calls) == 1
    assert len(executor_results) == 1
    assert len(chat_client.calls) == 1
    assert tuple(upstream_messages) == upstream_snapshot
    observation = json.loads(chat_client.calls[0][-1]["content"])
    assert observation == {
        "type": "verified_tool_result",
        "tool_name": GET_METADATA_TOOL_NAME,
        "success": executor_results[0].success,
        "data": executor_results[0].data,
        "error": None,
        "metadata": executor_results[0].metadata,
    }
    assert observation["data"] == {
        "scope": "project",
        "relative_path": "artifact.txt",
        "exists": True,
        "kind": "file",
        "size_bytes": target.stat().st_size,
    }
    assert content_marker not in repr(chat_client.calls)

    assert len(summaries) == 1
    summary = summaries[0]
    assert {
        key for key in summary if key.startswith("tool_")
    } == {
        "tool_call_count",
        "tool_name",
        "tool_risk_level",
        "tool_status",
        "tool_execution_ms",
    }
    assert summary["tool_name"] == GET_METADATA_TOOL_NAME
    assert summary["tool_status"] == "success"
    assert isinstance(summary["tool_execution_ms"], float)
    assert summary["agent_brain_profile"] == "agent_brain"
    assert summary["agent_brain_provider"] == "deepseek"
    assert summary["agent_brain_model"] == "deepseek-v4-flash"
    assert summary["chat_profile"] == summary["profile"] == "reasoning_strong"
    assert summary["chat_provider"] == summary["provider"] == "packycode"
    assert summary["chat_model"] == summary["model"] == "gpt-5.6-sol"
    serialized_summary = json.dumps(summary, ensure_ascii=False)
    assert "relative_path" not in serialized_summary
    assert "artifact.txt" not in serialized_summary
    assert content_marker not in serialized_summary
    assert user_marker not in serialized_summary


def test_factory_and_runtime_do_not_import_deepseek() -> None:
    source_root = Path(__file__).parents[1] / "src" / "jarvis_core" / "agent"

    for filename in ("factory.py", "runtime.py"):
        source = (source_root / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "jarvis_core.llm.deepseek" not in imported_modules
