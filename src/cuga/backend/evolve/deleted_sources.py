"""Retry committed conversation-deletion receipts until Evolve acknowledges them."""

import asyncio
from loguru import logger


async def deliver_source_deletions():
    from cuga.backend.evolve.integration import EvolveIntegration
    from cuga.backend.server.conversation_history import get_conversation_db

    if not EvolveIntegration.is_enabled():
        return
    db = get_conversation_db()
    for event in await db.pending_source_deletions():
        result = await EvolveIntegration._call_structured_tool(
            "record_source_deletion",
            {
                "namespace_id": event["instance_id"],
                "source_id": event["thread_id"],
                "agent_id": event["agent_id"],
                "user_id": event["user_id"],
                "deleted_at": event["deleted_at"],
            },
        )
        if not isinstance(result, dict) or result.get("recorded") is not True:
            raise RuntimeError("Evolve did not acknowledge source deletion")
        await db.acknowledge_source_deletion(event["event_id"])


async def source_deletion_delivery_loop():
    while True:
        try:
            await deliver_source_deletions()
        except Exception:
            logger.warning("Source deletion delivery failed; committed events will be retried")
        await asyncio.sleep(30)
