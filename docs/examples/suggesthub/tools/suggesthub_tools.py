from __future__ import annotations

from langchain_core.tools import tool

from docs.examples.suggesthub.app.database import db_session
from docs.examples.suggesthub.app import repository
from docs.examples.suggesthub.tools.dedupe import build_draft_from_text, find_similar


@tool
def find_similar_suggestions(query: str, limit: int = 3) -> dict:
    """Find existing SuggestHub suggestions that are semantically or lexically similar to the employee issue."""
    with db_session() as conn:
        return {"matches": find_similar(conn, query=query, limit=limit)}


@tool
def save_suggestion_draft(
    thread_id: str,
    raw_text: str,
    title: str,
    description: str,
    category: str,
    location: str,
    impact: str,
    similar_suggestion_ids: list[int] | None = None,
) -> dict:
    """Save a structured suggestion draft after Bob has asked clarifying questions and checked for duplicates."""
    with db_session() as conn:
        return repository.save_draft(
            conn,
            {
                "thread_id": thread_id,
                "raw_text": raw_text,
                "title": title,
                "description": description,
                "category": category,
                "location": location,
                "impact": impact,
                "similar_suggestion_ids": similar_suggestion_ids or [],
            },
        )


@tool
def publish_suggestion(draft_id: int, author_name: str = "Anonymous IBMer") -> dict:
    """Publish an employee-approved suggestion draft to the public hub feed."""
    with db_session() as conn:
        return repository.publish_draft(conn, draft_id=draft_id, author_name=author_name)


@tool
def upvote_suggestion(suggestion_id: int, visitor_id: str) -> dict:
    """Add the employee's vote to an existing suggestion. Duplicate votes from the same visitor are ignored."""
    with db_session() as conn:
        return repository.vote(conn, suggestion_id=suggestion_id, visitor_id=visitor_id)


@tool
def get_trending_suggestions(location: str | None = None, limit: int = 5) -> dict:
    """Return the top trending suggestions for a manager or employee view."""
    with db_session() as conn:
        return {"suggestions": repository.list_suggestions(conn, location=location, sort="trending")[:limit]}


@tool
def draft_manager_response(suggestion_id: int, intended_status: str) -> dict:
    """Draft a concise public manager response for a suggestion and intended status."""
    with db_session() as conn:
        suggestion = repository.get_suggestion(conn, suggestion_id)
    if not suggestion:
        return {"error": "Suggestion not found"}
    response = (
        f"We reviewed '{suggestion['title']}' for {suggestion['location']}. "
        f"Status: {intended_status}. The reported impact is: {suggestion['impact']}. "
        "Next step: we will share the owner and timing once confirmed."
    )
    return {"suggestion": suggestion, "draft_response": response}


@tool
def update_suggestion_status(
    suggestion_id: int,
    status: str,
    response: str,
    manager_name: str = "Facilities Manager",
) -> dict:
    """Update a suggestion's public status and add a manager response visible to all employees."""
    with db_session() as conn:
        return repository.update_status(
            conn,
            suggestion_id=suggestion_id,
            status=status,
            response=response,
            manager_name=manager_name,
        )


SUGGESTHUB_TOOLS = [
    find_similar_suggestions,
    save_suggestion_draft,
    publish_suggestion,
    upvote_suggestion,
    get_trending_suggestions,
    draft_manager_response,
    update_suggestion_status,
]


def deterministic_intake(raw_text: str, thread_id: str) -> dict:
    """Fallback intake path used by the API when an LLM is unavailable."""
    with db_session() as conn:
        matches = find_similar(conn, raw_text, limit=3)
        draft_data = build_draft_from_text(raw_text, thread_id=thread_id)
        draft_data["similar_suggestion_ids"] = [item["id"] for item in matches]
        draft = repository.save_draft(conn, draft_data)
    if matches:
        top = matches[0]
        reply = (
            f"I found a similar suggestion: '{top['title']}' with {top['vote_count']} votes. "
            "If this is the same issue, upvote it. If it is different, review the draft below and publish it."
        )
    else:
        reply = "I structured your idea into a draft. Review it, then publish it when it looks right."
    return {"reply": reply, "matches": matches, "draft": draft}
