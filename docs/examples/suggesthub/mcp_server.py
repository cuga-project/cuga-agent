from __future__ import annotations

import sys
import traceback
from typing import Any

# MCP stdio reserves stdout for protocol frames. Keep import-time logging/noise
# away from stdout until the server takes over the stream.
_PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr

from fastmcp import FastMCP

from docs.examples.suggesthub.app import repository
from docs.examples.suggesthub.app.database import db_session, init_db
from docs.examples.suggesthub.app.seed import seed_database
from docs.examples.suggesthub.tools.dedupe import build_draft_from_text, find_similar

mcp = FastMCP(
    "SuggestHub",
    instructions=(
        "IBM SuggestHub tools for Bob intake, semantic dedupe, draft creation, "
        "publishing employee-approved suggestions, voting, and manager responses."
    ),
)


def _ensure_db() -> None:
    init_db()
    with db_session() as conn:
        seed_database(conn)


@mcp.tool()
def find_similar_suggestions(query: str, limit: int = 3) -> dict[str, Any]:
    """Find existing suggestions similar to an employee's workplace improvement issue."""
    _ensure_db()
    with db_session() as conn:
        return {"matches": find_similar(conn, query=query, limit=limit)}


@mcp.tool()
def save_suggestion_draft(
    thread_id: str,
    raw_text: str,
    title: str,
    description: str,
    category: str,
    location: str,
    impact: str,
    similar_suggestion_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Save a structured suggestion draft after duplicate checking and clarification."""
    _ensure_db()
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


@mcp.tool()
def create_draft_from_employee_issue(raw_text: str, thread_id: str = "suggesthub-chat") -> dict[str, Any]:
    """Create a draft from natural language after checking for duplicate suggestions."""
    _ensure_db()
    with db_session() as conn:
        matches = find_similar(conn, raw_text, limit=3)
        draft_data = build_draft_from_text(raw_text, thread_id=thread_id)
        draft_data["similar_suggestion_ids"] = [item["id"] for item in matches]
        draft = repository.save_draft(conn, draft_data)
        return {"draft": draft, "matches": matches}


@mcp.tool()
def publish_suggestion(draft_id: int, author_name: str = "Anonymous IBMer") -> dict[str, Any]:
    """Publish an employee-approved suggestion draft to the public hub."""
    _ensure_db()
    with db_session() as conn:
        return repository.publish_draft(conn, draft_id=draft_id, author_name=author_name)


@mcp.tool()
def upvote_suggestion(suggestion_id: int, visitor_id: str = "cuga-chat-user") -> dict[str, Any]:
    """Upvote an existing suggestion. Duplicate votes from the same visitor are ignored."""
    _ensure_db()
    with db_session() as conn:
        return repository.vote(conn, suggestion_id=suggestion_id, visitor_id=visitor_id)


@mcp.tool()
def get_trending_suggestions(location: str | None = None, limit: int = 5) -> dict[str, Any]:
    """Return trending suggestions for the public hub or manager triage."""
    _ensure_db()
    with db_session() as conn:
        return {"suggestions": repository.list_suggestions(conn, location=location, sort="trending")[:limit]}


@mcp.tool()
def draft_manager_response(suggestion_id: int, intended_status: str) -> dict[str, Any]:
    """Draft a concise public manager response for a suggestion and intended status."""
    _ensure_db()
    with db_session() as conn:
        suggestion = repository.get_suggestion(conn, suggestion_id)
    if not suggestion:
        return {"error": "Suggestion not found"}
    response = (
        f"Thanks for raising '{suggestion['title']}'. We reviewed the impact for "
        f"{suggestion['location']} and are moving this to {intended_status}. "
        "We will post the next update when the owner and timing are confirmed."
    )
    return {"suggestion": suggestion, "draft_response": response}


@mcp.tool()
def update_suggestion_status(
    suggestion_id: int,
    status: str,
    response: str,
    manager_name: str = "Demo Manager",
) -> dict[str, Any]:
    """Update a suggestion's status and add a public manager response."""
    _ensure_db()
    with db_session() as conn:
        return repository.update_status(
            conn,
            suggestion_id=suggestion_id,
            status=status,
            response=response,
            manager_name=manager_name,
        )


if __name__ == "__main__":
    try:
        _ensure_db()
        sys.stdout = _PROTOCOL_STDOUT
        mcp.run(transport="stdio", show_banner=False)
    except Exception:
        sys.stdout = sys.stderr
        traceback.print_exc(file=sys.stderr)
        with open("suggesthub-mcp-error.log", "a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        raise
