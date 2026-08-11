from __future__ import annotations

from jarvis_core.agent.brain import StructuredAgentDecisionModel
from jarvis_core.agent.context import (
    AgentRuntimeMetadata,
    MinimalAgentContextBuilder,
)
from jarvis_core.agent.runtime import AgentRuntime
from jarvis_core.llm.client import LLMClient, StructuredLLMClient
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.tools.registry import ToolRegistry
from jarvis_core.tools.runtime_info import register_runtime_info_tool
from jarvis_core.version import JARVIS_VERSION


def create_agent_response_runtime(
    *,
    brain_client: StructuredLLMClient,
    brain_profile: ModelProfile,
    chat_client: LLMClient,
    chat_profile: ModelProfile,
) -> AgentRuntime:
    registry = ToolRegistry()
    register_runtime_info_tool(registry, chat_profile=chat_profile)
    return AgentRuntime(
        brain=StructuredAgentDecisionModel(brain_client),
        context_builder=MinimalAgentContextBuilder(
            metadata=AgentRuntimeMetadata(
                jarvis_version=JARVIS_VERSION,
                runtime_status="running",
            )
        ),
        registry=registry,
        chat_client=chat_client,
        brain_profile=brain_profile,
        chat_profile=chat_profile,
    )
