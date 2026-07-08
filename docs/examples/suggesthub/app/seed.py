from __future__ import annotations

import sqlite3

from docs.examples.suggesthub.app.database import init_db


SEED_SUGGESTIONS = [
    {
        "title": "Fix broken standing desks on Floor 3",
        "description": (
            "Several standing desks on Floor 3 no longer raise or lower reliably. "
            "Employees avoid the shared work area or lose time finding functional desks."
        ),
        "category": "Facilities",
        "location": "SVL Floor 3",
        "impact": "Daily workstation availability and ergonomics for hybrid teams.",
        "status": "Resolved",
        "author_name": "Priya S.",
        "resolved_at": "2026-06-22T17:30:00Z",
        "votes": ["seed-1", "seed-2", "seed-3", "seed-4", "seed-5", "seed-6", "seed-7"],
        "updates": [
            (
                "Under Review",
                "Facilities confirmed the issue and is auditing all adjustable desks on Floor 3.",
                "Dana Lee",
            ),
            (
                "Resolved",
                "47 employees helped identify the pattern. Facilities repaired 11 desks and added a QR code for faster future reporting.",
                "Dana Lee",
            ),
        ],
    },
    {
        "title": "Improve guest Wi-Fi reliability in Austin conference rooms",
        "description": "Guest Wi-Fi drops during customer workshops in the Austin client center.",
        "category": "IT",
        "location": "Austin",
        "impact": "Customer-facing meetings lose time when external attendees reconnect repeatedly.",
        "status": "In Progress",
        "author_name": "Marcus R.",
        "resolved_at": None,
        "votes": ["seed-8", "seed-9", "seed-10", "seed-11", "seed-12"],
        "updates": [
            ("Under Review", "Network team is checking access point load during workshop hours.", "IT Ops"),
            ("In Progress", "A firmware update is scheduled for the conference room access points.", "IT Ops"),
        ],
    },
    {
        "title": "Add healthier grab-and-go breakfast options",
        "description": "The cafeteria has limited high-protein and low-sugar breakfast choices before 9 AM.",
        "category": "Food & Beverage",
        "location": "NYC",
        "impact": "Employees in early meetings skip breakfast or leave the office to find alternatives.",
        "status": "New",
        "author_name": "Anonymous IBMer",
        "resolved_at": None,
        "votes": ["seed-13", "seed-14", "seed-15", "seed-16"],
        "updates": [],
    },
    {
        "title": "Publish a clear process for badge access issues",
        "description": "Employees do not know whether to contact security, facilities, or IT when badge access breaks.",
        "category": "Safety",
        "location": "SVL",
        "impact": "People lose time at building entrances and managers handle avoidable escalations.",
        "status": "Under Review",
        "author_name": "Lin T.",
        "resolved_at": None,
        "votes": ["seed-17", "seed-18", "seed-19"],
        "updates": [("Under Review", "Security operations is drafting a single escalation path.", "Security Ops")],
    },
]


def seed_database(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0] > 0:
        return

    for item in SEED_SUGGESTIONS:
        cur = conn.execute(
            """
            INSERT INTO suggestions
                (title, description, category, location, impact, status, author_name, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["title"],
                item["description"],
                item["category"],
                item["location"],
                item["impact"],
                item["status"],
                item["author_name"],
                item["resolved_at"],
            ),
        )
        suggestion_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO votes (suggestion_id, visitor_id) VALUES (?, ?)",
            [(suggestion_id, voter) for voter in item["votes"]],
        )
        conn.executemany(
            """
            INSERT INTO status_updates (suggestion_id, status, response, manager_name)
            VALUES (?, ?, ?, ?)
            """,
            [(suggestion_id, status, response, manager) for status, response, manager in item["updates"]],
        )
    conn.commit()


def reset_seeded_database(path: str | None = None) -> None:
    init_db(reset=True, path=path)
    from docs.examples.suggesthub.app.database import db_session

    with db_session(path) as conn:
        seed_database(conn)
