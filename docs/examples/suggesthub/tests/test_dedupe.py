from docs.examples.suggesthub.app.database import db_session
from docs.examples.suggesthub.tools.dedupe import build_draft_from_text, find_similar, lexical_similarity


def test_lexical_similarity_matches_related_workplace_issue():
    score = lexical_similarity(
        "standing desks on floor 3 are broken",
        "Fix broken standing desks on Floor 3",
    )

    assert score > 0.45


def test_find_similar_matches_seeded_standing_desk_issue(db_path):
    with db_session(db_path) as conn:
        matches = find_similar(conn, "The adjustable standing desks on floor 3 are always broken")

    assert matches
    assert "standing desks" in matches[0]["title"].lower()
    assert matches[0]["similarity_source"] in {"lexical", "embedding"}


def test_build_draft_infers_category_and_location():
    draft = build_draft_from_text("The standing desks on floor 3 are broken", thread_id="t1")

    assert draft["category"] == "Facilities"
    assert draft["location"] == "Floor 3"
    assert draft["thread_id"] == "t1"
