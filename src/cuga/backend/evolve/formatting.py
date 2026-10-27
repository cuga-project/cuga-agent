"""Evolve output formatting utilities for prompt injection."""

import re
from typing import Any, Sequence

from langchain_core.messages import BaseMessage, HumanMessage


def get_first_human_message_content(messages: Sequence[BaseMessage] | None) -> str:
    if not messages:
        return ""

    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = getattr(msg, "content", "") or ""
            if isinstance(content, str) and content:
                return content
    return ""


def get_latest_memory_query(messages: Sequence[BaseMessage] | None) -> str:
    if not messages:
        return ""

    from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
        EMPTY_RESPONSE_CORRECTION,
        EXECUTION_OUTPUT_PREFIX,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.reflection.verify_result import (
        VERIFY_BLOCKED_PREFIX,
    )

    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage):
            continue
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        if content.startswith(EXECUTION_OUTPUT_PREFIX):
            continue
        if content.startswith(VERIFY_BLOCKED_PREFIX):
            continue
        if content.startswith(EMPTY_RESPONSE_CORRECTION):
            continue
        if content:
            return content
    return ""


def _format_user_preferences(categories: dict | None) -> tuple[str, list[str]]:
    """Render facts and collect IDs in the same pass so omitted facts never count as used."""
    sections = []
    entity_ids = []
    for category, facts in (categories or {}).items():
        if not isinstance(facts, list):
            continue
        lines = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            content = str(fact.get("content") or "").strip()
            key = str(fact.get("key") or "").strip()
            value = str(fact.get("value") or "").strip()
            text = content or (f"{key}: {value}" if key and value else key or value)
            if not text:
                continue
            lines.append(f"- {text}")
            if fact.get("id"):
                entity_ids.append(str(fact["id"]))
        if lines:
            title = str(category or "misc").replace("_", " ").title()
            sections.append(f"{title}:\n" + "\n".join(lines))
    if not sections:
        return "", []
    return (
        "Use this as durable user preference/profile context when it is relevant to the answer or decision-making.\n"
        "If any remembered preference conflicts with the latest user message, follow the latest user message.\n\n"
        + "\n\n".join(sections),
        list(dict.fromkeys(entity_ids)),
    )


def format_evolve_user_preference(categories: dict[str, list[dict[str, Any]]] | None) -> str:
    return _format_user_preferences(categories)[0]


def build_evolve_user_preference_with_attribution(categories: dict | None) -> tuple[str, list[str]]:
    text, entity_ids = _format_user_preferences(categories)
    return (f"\n\n## Evolve User Preference\n{text}", entity_ids) if text else ("", [])


def build_evolve_user_preference_section(categories: dict[str, list[dict[str, Any]]] | None) -> str:
    return build_evolve_user_preference_with_attribution(categories)[0]


def parse_evolve_guideline_items(raw_guidelines: str) -> list[str]:
    if not raw_guidelines or not raw_guidelines.strip():
        return []

    guideline_items: list[str] = []
    current_item: list[str] = []
    fallback_lines: list[str] = []

    def flush_current_item() -> None:
        if current_item:
            combined = " ".join(part.strip() for part in current_item if part.strip()).strip()
            if combined:
                guideline_items.append(combined)
            current_item.clear()

    for raw_line in raw_guidelines.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush_current_item()
            continue

        if line.startswith("#"):
            continue

        numbered_or_bullet = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", line)
        if numbered_or_bullet:
            flush_current_item()
            current_item.append(numbered_or_bullet.group(1).strip())
            continue

        if current_item:
            current_item.append(line)
        else:
            fallback_lines.append(line)

    flush_current_item()

    if guideline_items:
        return guideline_items

    fallback_text = "\n".join(fallback_lines).strip()
    if not fallback_text:
        return []

    fallback_text = re.sub(r"^Guidelines\s+for:\s*.+$", "", fallback_text, flags=re.IGNORECASE | re.MULTILINE)
    fallback_text = fallback_text.strip()
    if not fallback_text:
        return []

    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", fallback_text) if paragraph.strip()]
    if paragraphs:
        return paragraphs

    return [fallback_text]


def format_evolve_guidelines(raw_guidelines: str) -> str:
    guideline_items = parse_evolve_guideline_items(raw_guidelines)
    if not guideline_items:
        return ""

    formatted_items = "\n".join(
        f"{index}. {guideline}" for index, guideline in enumerate(guideline_items, start=1)
    )

    return (
        "Here are some useful guidelines derived from past experiences where similar tasks "
        "resulted in overall failure or success. Each guideline is a concrete "
        "recommendation to apply in the current task.\n\n"
        "```text\n"
        "guideline: <Recommendation that should be applied to avoid repeated mistakes>\n"
        "```\n\n"
        f"{formatted_items}\n\n"
        "**Guideline Usage:** These guidelines are hard earned experience from previous similar tasks. "
        "**Do not ignore them.** It is very important that you use them to adjust your "
        "reasoning or code generation process so similar issues are avoided in the current task.\n"
        "These guidelines are as important as the instructions and as important as few-shot examples."
    )


def build_evolve_guidelines_section(raw_guidelines: str) -> str:
    formatted_guidelines = format_evolve_guidelines(raw_guidelines)
    if not formatted_guidelines:
        return ""
    return f"\n\n## Evolve Guidelines\n{formatted_guidelines}"
