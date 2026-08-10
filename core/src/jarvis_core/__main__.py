import asyncio
import logging
import os
import sys
from pathlib import Path

from jarvis_core.conversation import Conversation, LLMConversation
from jarvis_core.llm.config import find_project_root, load_deepseek_settings
from jarvis_core.llm.deepseek import DeepSeekClient, DeepSeekStructuredClient
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
    settings = load_deepseek_settings(project_root)
    memory_store = SQLiteMemoryStore(
        resolve_memory_database_path(data_dir=data_dir)
    )
    return LLMConversation(
        DeepSeekClient(settings),
        personality_instructions=JARVIS_PERSONALITY_INSTRUCTIONS,
        capability_constraints=CURRENT_RUNTIME_CAPABILITY_CONSTRAINTS,
        memory_store=memory_store,
        memory_router=SemanticMemoryIntentRouter(
            DeepSeekStructuredClient(settings)
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
