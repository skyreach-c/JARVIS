import ast
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from jarvis_core.agent.brain import StructuredAgentDecisionModel
from jarvis_core.agent.factory import create_agent_response_runtime
from jarvis_core.llm.client import ChatMessage
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.tools.contracts import ToolCall
from jarvis_core.tools.runtime_info import RUNTIME_INFO_TOOL_NAME


class FakeStructuredClient:
    async def complete_json(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
    ) -> str:
        del messages, max_tokens
        return '{"action":"respond","tool_name":null,"arguments":{}}'


class FakeChatClient:
    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[str]:
        del messages
        yield "answer"


async def test_factory_binds_profiles_brain_chat_and_runtime_info() -> None:
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
    )

    assert isinstance(runtime.brain, StructuredAgentDecisionModel)
    assert runtime.brain_profile is brain_profile
    assert runtime.chat_client is chat_client
    assert runtime.chat_profile is chat_profile
    assert tuple(definition.name for definition in runtime.registry.definitions()) == (
        RUNTIME_INFO_TOOL_NAME,
    )
    result = await runtime.registry.execute(
        ToolCall(tool_name=RUNTIME_INFO_TOOL_NAME, arguments={}),
        request_id="factory-runtime-info",
    )
    assert result.success is True
    assert result.data is not None
    assert result.data["chat_profile"] == "reasoning_strong"
    assert result.data["provider"] == "packycode"
    assert result.data["model"] == "gpt-5.6-sol"


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
