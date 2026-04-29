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

    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage):
            continue
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        if content.startswith("Execution output:"):
            continue
        if content:
            return content
    return ""


def format_evolve_user_preference(categories: dict[str, list[dict[str, Any]]] | None) -> str:
    if not categories:
        return ""

    lines = [
        "Use this as durable user preference/profile context when it is relevant to the answer or decision-making.",
        "If any remembered preference conflicts with the latest user message, follow the latest user message.",
        "",
    ]

    for category, facts in categories.items():
        if not facts:
            continue
        category_title = str(category or "misc").replace("_", " ").title()
        lines.append(f"{category_title}:")
        for fact in facts:
            content = str((fact or {}).get("content") or "").strip()
            key = str((fact or {}).get("key") or "").strip()
            value = str((fact or {}).get("value") or "").strip()

            if content:
                lines.append(f"- {content}")
            elif key and value:
                lines.append(f"- {key}: {value}")
            elif key:
                lines.append(f"- {key}")
            elif value:
                lines.append(f"- {value}")
        lines.append("")

    while lines and not lines[-1]:
        lines.pop()

    if len(lines) <= 2:
        return ""

    return "\n".join(lines)


def build_evolve_user_preference_section(
    categories: dict[str, list[dict[str, Any]]] | None,
) -> str:
    preference_text = format_evolve_user_preference(categories)
    if not preference_text:
        return ""
    return f"\n\n## Evolve User Preference\n{preference_text}"


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
