from cuga.backend.memory.memory import get_kaizen_client


def get_formatted_tips(namespace_id: str, agent_id: str, query: str, limit: int) -> str | None:
    """Return tips formatted for prompt injection."""
    client = get_kaizen_client()
    tips = client.search_entities(
        namespace_id=namespace_id,
        query=query,
        filters={"__entity_type": "tip", "metadata.agent": agent_id, "metadata.user_id": "100"},
        limit=limit,
    )
    if not tips:
        tips = client.search_entities(
            namespace_id=namespace_id,
            query=query,
            filters={"__entity_type": "tip", "metadata.user_id": "100"},
            limit=limit,
        )
    if not tips:
        return None
    lines = [f"{idx}. {tip.content}" for idx, tip in enumerate(tips, start=1)]
    return "\nRelevant Tips:\n" + "\n".join(lines)
