import re
from typing import Any, Dict, List, Optional


DEFAULT_DECISION_CONTEXT_MAX_FACTS = 5


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _escape_inline_code(value: str) -> str:
    return value.replace("`", "'")


def _format_fact_pointer(fact: Dict[str, str]) -> str:
    category = _safe_text(fact.get("category")) or "misc"
    key = _safe_text(fact.get("key"))
    if key:
        return f"{category}.{key}"
    return category


def extract_structured_facts(preferences: Dict[str, Any]) -> List[Dict[str, str]]:
    """Normalize user preferences into structured fact objects."""
    if not preferences:
        return []

    facts: List[Dict[str, str]] = []
    first_value = next(iter(preferences.values()), None)

    if isinstance(first_value, list):
        for category, category_facts in preferences.items():
            if not isinstance(category_facts, list):
                continue
            category_name = _safe_text(category) or "misc"
            for fact in category_facts:
                if isinstance(fact, dict):
                    key = _safe_text(fact.get("key"))
                    value = _safe_text(fact.get("value"))
                    content = _safe_text(fact.get("content"))
                else:
                    key = ""
                    value = ""
                    content = _safe_text(fact)

                if not any([key, value, content]):
                    continue

                facts.append(
                    {
                        "category": category_name,
                        "key": key,
                        "value": value,
                        "content": content,
                    }
                )
    else:
        for fact_id, content in preferences.items():
            text = _safe_text(content)
            if not text:
                continue
            facts.append(
                {
                    "category": "legacy",
                    "key": _safe_text(fact_id),
                    "value": "",
                    "content": text,
                }
            )

    deduped_facts: List[Dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for fact in facts:
        signature = (fact["category"], fact["key"], fact["value"], fact["content"])
        if signature in seen:
            continue
        seen.add(signature)
        deduped_facts.append(fact)
    return deduped_facts


def _tokenize_query(query: str) -> List[str]:
    if not query:
        return []
    return [token for token in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if len(token) > 1]


def _fact_search_text(fact: Dict[str, str]) -> str:
    parts = [
        _safe_text(fact.get("category")).lower(),
        _safe_text(fact.get("key")).lower(),
        _safe_text(fact.get("value")).lower(),
        _safe_text(fact.get("content")).lower(),
    ]
    return " ".join(part for part in parts if part)


def _fact_relevance_score(fact: Dict[str, str], query_tokens: List[str]) -> int:
    if not query_tokens:
        return 0

    category = _safe_text(fact.get("category")).lower()
    key = _safe_text(fact.get("key")).lower()
    value = _safe_text(fact.get("value")).lower()
    content = _safe_text(fact.get("content")).lower()
    search_text = f"{category} {key} {value} {content}"

    score = 0
    for token in query_tokens:
        if not token:
            continue
        if token in key:
            score += 8
        if token in value:
            score += 7
        if token in category:
            score += 5
        if token in content:
            score += 4
        if token in search_text:
            score += 1

    # De-prioritize noisy variable-dump facts when selecting very few facts.
    if category == "misc":
        if content.startswith("{") and "variable_name" in content:
            score -= 6
        else:
            score -= 1

    return score


def _select_relevant_facts(
    facts: List[Dict[str, str]], max_facts: int, query: Optional[str] = None
) -> List[Dict[str, str]]:
    if not facts or max_facts <= 0:
        return []

    query_tokens = _tokenize_query(query or "")
    if not query_tokens:
        return facts[:max_facts]

    scored = []
    for idx, fact in enumerate(facts):
        score = _fact_relevance_score(fact, query_tokens)
        category = _safe_text(fact.get("category")).lower()
        search_text = _fact_search_text(fact)
        # Prefer non-misc categories in tie cases.
        category_bonus = 1 if category and category != "misc" else 0
        scored.append((score, category_bonus, idx, fact, search_text))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [item[3] for item in scored[:max_facts]]

    # Fallback if scoring fails to surface anything meaningful.
    # Keep stable order for predictability.
    if all(item[0] <= 0 for item in scored[:max_facts]):
        return facts[:max_facts]

    return selected


def format_preferences_for_decision_context(
    preferences: Dict[str, Any],
    max_facts: int = DEFAULT_DECISION_CONTEXT_MAX_FACTS,
    query: Optional[str] = None,
) -> str:
    """Create compact memory context to guide planning decisions."""
    facts = extract_structured_facts(preferences)
    if not facts:
        return ""
    selected_facts = _select_relevant_facts(facts, max_facts=max_facts, query=query)

    lines = [
        "Use these persistent user facts to drive planning decisions and query scoping.",
        "Prefer specific filters from these facts over broad listing operations when possible.",
        "",
        "Structured facts:",
    ]
    for fact in selected_facts:
        pointer = _escape_inline_code(_format_fact_pointer(fact))
        value = _safe_text(fact.get("value"))
        content = _safe_text(fact.get("content"))
        if value:
            lines.append(f"- `{pointer}` = `{_escape_inline_code(value)}`")
        elif content:
            lines.append(f"- `{pointer}`: {content}")

    return "\n".join(lines)
