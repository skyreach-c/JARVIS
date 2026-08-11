import asyncio
import logging
import os
import sys
from pathlib import Path

from jarvis_core.agent.factory import create_agent_response_runtime
from jarvis_core.conversation import Conversation, LLMConversation
from jarvis_core.llm.config import find_project_root, load_llm_settings
from jarvis_core.llm.profiles import (
    build_model_profiles,
    create_chat_client,
    create_structured_client,
)
from jarvis_core.memory_router import SemanticMemoryIntentRouter
from jarvis_core.memory_store import (
    SQLiteMemoryStore,
    resolve_memory_database_path,
)
from jarvis_core.personality import JARVIS_PERSONALITY_INSTRUCTIONS
from jarvis_core.runtime_capabilities import CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS
from jarvis_core.server import JarvisCoreServer, emit_process_ready

logger = logging.getLogger(__name__)


def build_conversation(*, data_dir: Path | str | None = None) -> Conversation:
    project_root = find_project_root(Path(__file__))
    settings = load_llm_settings(project_root)
    profiles = build_model_profiles(
        deepseek_model=settings.deepseek.model,
        reasoning_strong_model=settings.reasoning_strong_model,
        reasoning_strong_effort=settings.reasoning_strong_effort,
    )
    chat_profile = profiles[settings.chat_profile]
    router_profile = profiles["structured_router"]
    brain_profile = profiles["agent_brain"]
    chat_client = create_chat_client(
        chat_profile,
        deepseek_settings=settings.deepseek,
        packycode_settings=settings.packycode,
    )
    router_client = create_structured_client(
        router_profile,
        deepseek_settings=settings.deepseek,
    )
    brain_client = create_structured_client(
        brain_profile,
        deepseek_settings=settings.deepseek,
    )
    agent_runtime = create_agent_response_runtime(
        brain_client=brain_client,
        brain_profile=brain_profile,
        chat_client=chat_client,
        chat_profile=chat_profile,
        project_root=project_root,
    )
    memory_store = SQLiteMemoryStore(
        resolve_memory_database_path(data_dir=data_dir)
    )
    return LLMConversation(
        agent_runtime,
        personality_instructions=JARVIS_PERSONALITY_INSTRUCTIONS,
        capability_constraints=CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS,
        memory_store=memory_store,
        chat_profile=chat_profile,
        memory_router_profile=router_profile,
        memory_router=SemanticMemoryIntentRouter(
            router_client
        ),
    )


async def run_core() -> None:
    auth_token = os.environ.get("JARVIS_AUTH_TOKEN")
    if not auth_token:
        raise RuntimeError("JARVIS_AUTH_TOKEN is required")

    server = JarvisCoreServer(
        auth_token=auth_token,
        conversation=build_conversation(),
    )
    await server.start()
    emit_process_ready(server.port, sys.stdout)
    logger.info("JARVIS Core listening on %s:%s", server.host, server.port)

    try:
        await asyncio.Future()
    finally:
        await server.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        asyncio.run(run_core())
    except KeyboardInterrupt:
        logger.info("JARVIS Core stopped")
    except Exception:
        logger.exception("JARVIS Core failed")
        raise


if __name__ == "__main__":
    main()
