import asyncio
import logging
import os
import sys

from jarvis_core.conversation import FakeConversation
from jarvis_core.server import JarvisCoreServer, emit_process_ready

logger = logging.getLogger(__name__)


async def run_core() -> None:
    auth_token = os.environ.get("JARVIS_AUTH_TOKEN")
    if not auth_token:
        raise RuntimeError("JARVIS_AUTH_TOKEN is required")

    server = JarvisCoreServer(
        auth_token=auth_token,
        conversation=FakeConversation(),
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
