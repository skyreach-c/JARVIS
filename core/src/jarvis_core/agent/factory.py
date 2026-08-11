from __future__ import annotations

from pathlib import Path

from jarvis_core.agent.brain import StructuredAgentDecisionModel
from jarvis_core.agent.context import (
    AgentRuntimeMetadata,
    MinimalAgentContextBuilder,
)
from jarvis_core.agent.runtime import AgentRuntime
from jarvis_core.llm.client import LLMClient, StructuredLLMClient
from jarvis_core.llm.profiles import ModelProfile
from jarvis_core.tools.filesystem import (
    register_get_metadata_tool,
    register_list_directory_tool,
)
from jarvis_core.tools.project_files import ProjectPathPolicy
from jarvis_core.tools.registry import ToolRegistry
from jarvis_core.tools.runtime_info import register_runtime_info_tool
from jarvis_core.tools.system_info import register_os_info_tool
from jarvis_core.version import JARVIS_VERSION


def create_agent_response_runtime(
    *,
    brain_client: StructuredLLMClient,
    brain_profile: ModelProfile,
    chat_client: LLMClient,
    chat_profile: ModelProfile,
    project_root: Path,
) -> AgentRuntime:
    registry = ToolRegistry()
    path_policy = ProjectPathPolicy(project_root)
    register_runtime_info_tool(registry, chat_profile=chat_profile)
    register_os_info_tool(registry)
    register_list_directory_tool(registry, path_policy=path_policy)
    register_get_metadata_tool(registry, path_policy=path_policy)
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
