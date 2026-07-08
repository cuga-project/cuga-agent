from docs.examples.suggesthub.app import repository
from docs.examples.suggesthub.app.database import db_session


def test_create_vote_and_prevent_duplicate_votes(db_path):
    with db_session(db_path) as conn:
        suggestion = repository.create_suggestion(
            conn,
            {
                "title": "Add monitors to focus rooms",
                "description": "Focus rooms need monitors for pair programming.",
                "category": "IT",
                "location": "SVL",
                "impact": "Hybrid collaboration is slower without room displays.",
            },
        )
        first_vote = repository.vote(conn, suggestion["id"], "visitor-1")
        second_vote = repository.vote(conn, suggestion["id"], "visitor-1")

    assert first_vote["inserted"] is True
    assert second_vote["inserted"] is False
    assert second_vote["suggestion"]["vote_count"] == 1


def test_status_update_creates_public_timeline_entry(db_path):
    with db_session(db_path) as conn:
        suggestion = repository.list_suggestions(conn)[0]
        updated = repository.update_status(
            conn,
            suggestion_id=suggestion["id"],
            status="In Progress",
            response="Owner assigned and timeline is being confirmed.",
            manager_name="Demo Manager",
        )

    assert updated["status"] == "In Progress"
    assert updated["updates"][-1]["response"] == "Owner assigned and timeline is being confirmed."
    assert updated["updates"][-1]["manager_name"] == "Demo Manager"


def test_manager_summary_returns_status_counts(db_path):
    with db_session(db_path) as conn:
        summary = repository.manager_summary(conn)

    assert summary["total_suggestions"] >= 4
    assert summary["status_counts"]["Resolved"] >= 1
    assert summary["top"]
