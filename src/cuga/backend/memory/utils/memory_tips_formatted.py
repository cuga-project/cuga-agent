from cuga.backend.memory.memory import Memory


def get_formatted_tips(namespace_id: str, agent_id: str, query: str, limit: int) -> str | None:
    """Return tips formatted for prompt injection."""
    memory = Memory()
    tips = memory.get_matching_tips(namespace_id=namespace_id, agent_id=agent_id, query=query, limit=limit)
    if not tips:
        return None
    lines = [f"{idx}. {tip}" for idx, tip in enumerate(tips, start=1)]
    return "\nRelevant Tips:\n" + "\n".join(lines)
