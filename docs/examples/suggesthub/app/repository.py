from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from docs.examples.suggesthub.app.database import row_to_dict, rows_to_dicts


VALID_STATUSES = {"New", "Under Review", "In Progress", "Resolved", "Declined"}
VALID_CATEGORIES = {"Facilities", "IT", "Wellness", "Food & Beverage", "Safety", "Culture", "Other"}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _with_vote_count(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict | None:
    item = row_to_dict(row)
    if not item:
        return None
    item["vote_count"] = conn.execute(
        "SELECT COUNT(*) FROM votes WHERE suggestion_id = ?",
        (item["id"],),
    ).fetchone()[0]
    item["updates"] = rows_to_dicts(
        conn.execute(
            "SELECT * FROM status_updates WHERE suggestion_id = ? ORDER BY id",
            (item["id"],),
        ).fetchall()
    )
    return item


def create_suggestion(conn: sqlite3.Connection, data: dict) -> dict:
    category = data.get("category") if data.get("category") in VALID_CATEGORIES else "Other"
    cur = conn.execute(
        """
        INSERT INTO suggestions
            (title, description, category, location, impact, author_name, anonymous)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["title"].strip(),
            data["description"].strip(),
            category,
            data.get("location", "Unknown").strip() or "Unknown",
            data.get("impact", "Needs manager review").strip() or "Needs manager review",
            data.get("author_name", "Anonymous IBMer").strip() or "Anonymous IBMer",
            1 if data.get("anonymous") else 0,
        ),
    )
    conn.commit()
    return get_suggestion(conn, cur.lastrowid)


def get_suggestion(conn: sqlite3.Connection, suggestion_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    return _with_vote_count(conn, row)


def list_suggestions(
    conn: sqlite3.Connection,
    location: str | None = None,
    category: str | None = None,
    status: str | None = None,
    sort: str = "votes",
) -> list[dict]:
    clauses = []
    params: list[str] = []
    if location:
        clauses.append("location LIKE ?")
        params.append(f"%{location}%")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = {
        "newest": "s.created_at DESC",
        "status": "s.status ASC, vote_count DESC",
        "votes": "vote_count DESC, s.created_at DESC",
        "trending": "recent_votes DESC, vote_count DESC",
    }.get(sort, "vote_count DESC, s.created_at DESC")
    rows = conn.execute(
        f"""
        SELECT
            s.*,
            COUNT(v.visitor_id) AS vote_count,
            SUM(CASE WHEN v.created_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END) AS recent_votes
        FROM suggestions s
        LEFT JOIN votes v ON v.suggestion_id = s.id
        {where}
        GROUP BY s.id
        ORDER BY {order}
        """,
        params,
    ).fetchall()
    items = rows_to_dicts(rows)
    for item in items:
        item["updates"] = rows_to_dicts(
            conn.execute(
                "SELECT * FROM status_updates WHERE suggestion_id = ? ORDER BY id",
                (item["id"],),
            ).fetchall()
        )
    return items


def vote(conn: sqlite3.Connection, suggestion_id: int, visitor_id: str) -> dict:
    if not get_suggestion(conn, suggestion_id):
        raise ValueError("Suggestion not found")
    inserted = False
    try:
        conn.execute(
            "INSERT INTO votes (suggestion_id, visitor_id) VALUES (?, ?)",
            (suggestion_id, visitor_id),
        )
        conn.commit()
        inserted = True
    except sqlite3.IntegrityError:
        inserted = False
    suggestion = get_suggestion(conn, suggestion_id)
    return {"inserted": inserted, "suggestion": suggestion}


def save_draft(conn: sqlite3.Connection, data: dict) -> dict:
    cur = conn.execute(
        """
        INSERT INTO bob_drafts
            (thread_id, raw_text, title, description, category, location, impact, similar_suggestion_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["thread_id"],
            data["raw_text"],
            data["title"],
            data["description"],
            data["category"],
            data["location"],
            data["impact"],
            ",".join(str(x) for x in data.get("similar_suggestion_ids", [])),
        ),
    )
    conn.commit()
    return row_to_dict(conn.execute("SELECT * FROM bob_drafts WHERE id = ?", (cur.lastrowid,)).fetchone())


def latest_draft(conn: sqlite3.Connection, thread_id: str) -> dict | None:
    return row_to_dict(
        conn.execute(
            "SELECT * FROM bob_drafts WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
    )


def publish_draft(conn: sqlite3.Connection, draft_id: int, author_name: str = "Anonymous IBMer") -> dict:
    draft = row_to_dict(conn.execute("SELECT * FROM bob_drafts WHERE id = ?", (draft_id,)).fetchone())
    if not draft:
        raise ValueError("Draft not found")
    suggestion = create_suggestion(
        conn,
        {
            "title": draft["title"],
            "description": draft["description"],
            "category": draft["category"],
            "location": draft["location"],
            "impact": draft["impact"],
            "author_name": author_name,
        },
    )
    conn.execute("UPDATE bob_drafts SET approved = 1 WHERE id = ?", (draft_id,))
    conn.commit()
    return suggestion


def update_status(
    conn: sqlite3.Connection,
    suggestion_id: int,
    status: str,
    response: str,
    manager_name: str = "Facilities Manager",
) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if not get_suggestion(conn, suggestion_id):
        raise ValueError("Suggestion not found")
    resolved_at = utc_now() if status == "Resolved" else None
    conn.execute(
        "UPDATE suggestions SET status = ?, updated_at = ?, resolved_at = COALESCE(?, resolved_at) WHERE id = ?",
        (status, utc_now(), resolved_at, suggestion_id),
    )
    conn.execute(
        """
        INSERT INTO status_updates (suggestion_id, status, response, manager_name)
        VALUES (?, ?, ?, ?)
        """,
        (suggestion_id, status, response.strip(), manager_name.strip() or "Facilities Manager"),
    )
    conn.commit()
    return get_suggestion(conn, suggestion_id)


def manager_summary(conn: sqlite3.Connection, location: str | None = None) -> dict:
    suggestions = list_suggestions(conn, location=location, sort="trending")
    status_counts = {
        row["status"]: row["count"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM suggestions GROUP BY status",
        ).fetchall()
    }
    return {
        "location": location or "All locations",
        "top": suggestions[:5],
        "status_counts": status_counts,
        "total_suggestions": sum(status_counts.values()),
    }
