from cuga.backend.memory.memory import get_kaizen_client
from loguru import logger


def get_formatted_tips(namespace_id: str, agent_id: str, query: str, limit: int) -> str | None:
    """Return tips formatted for prompt injection."""
    normalized_agent_id = str(agent_id or "").strip()
    client = get_kaizen_client()
    try:
        # Retrieval can run before any write path initializes memory namespace.
        client.ensure_namespace(namespace_id=namespace_id)
    except Exception as e:
        logger.warning(
            "Memory guideline retrieval skipped: failed to ensure namespace '{}' ({})",
            namespace_id,
            e,
        )
        return None

    try:
        filters = {"type": "guideline"}
        if normalized_agent_id:
            filters["metadata.agent_id"] = normalized_agent_id
        tips = client.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters=filters,
            limit=limit,
        )
    except Exception as e:
        logger.warning(
            "Memory guideline retrieval failed namespace_id='{}' agent_id='{}' query='{}' error={}",
            namespace_id,
            normalized_agent_id,
            (query or "")[:120],
            e,
        )
        return None

    raw_count = len(tips)
    raw_types = [getattr(tip, "type", None) for tip in tips]
    raw_ids = [getattr(tip, "id", None) for tip in tips]
    tips = [tip for tip in tips if getattr(tip, "type", None) == "guideline"]
    if raw_count != len(tips):
        logger.warning(
            "Memory guideline retrieval dropped {} non-guideline result(s) namespace_id='{}' ids={} types={}",
            raw_count - len(tips),
            namespace_id,
            raw_ids,
            raw_types,
        )

    if not tips:
        logger.debug(
            "Memory guideline retrieval: no results namespace_id='{}' agent_id='{}' query='{}' limit={}",
            namespace_id,
            normalized_agent_id,
            (query or "")[:120],
            limit,
        )
        return None
    logger.debug(
        "Memory guideline retrieval: {} result(s) namespace_id='{}' agent_id='{}' query='{}' limit={}",
        len(tips),
        namespace_id,
        normalized_agent_id,
        (query or "")[:120],
        limit,
    )
    lines = [f"{idx}. {tip.content}" for idx, tip in enumerate(tips, start=1)]
    logger.debug(
        "Memory guideline retrieval tips:\n{}",
        "\n".join(lines),
    )
    return "\nRelevant Tips:\n" + "\n".join(lines)
