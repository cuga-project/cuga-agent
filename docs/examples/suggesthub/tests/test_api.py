class FakeResult:
    answer = "Mocked Bob reply"
    error = None


class FakeBob:
    async def invoke(self, message, thread_id=None):
        return FakeResult()


def test_bob_message_returns_draft_and_matches(client, monkeypatch):
    from docs.examples.suggesthub.app import main

    monkeypatch.setattr(main, "get_bob_agent", lambda: FakeBob())

    response = client.post(
        "/api/bob/message",
        json={
            "message": "The standing desks on floor 3 are broken",
            "thread_id": "thread-api-test",
            "visitor_id": "visitor-api-test",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Mocked Bob reply"
    assert body["draft"]["category"] == "Facilities"
    assert body["matches"]


def test_suggestions_filter_vote_and_manager_status(client):
    suggestions = client.get("/api/suggestions?location=SVL").json()
    assert suggestions
    suggestion_id = suggestions[0]["id"]

    vote = client.post(
        f"/api/suggestions/{suggestion_id}/vote",
        json={"visitor_id": "api-voter"},
    )
    assert vote.status_code == 200
    assert vote.json()["inserted"] is True

    status = client.post(
        "/api/manager/status",
        json={
            "suggestion_id": suggestion_id,
            "status": "Under Review",
            "response": "We are reviewing scope and owner.",
            "manager_email": "manager@ibm.com",
            "manager_name": "Demo Manager",
        },
    )
    assert status.status_code == 200
    assert status.json()["status"] == "Under Review"


def test_manager_summary_and_resolved_story(client):
    denied = client.get("/api/manager/summary")
    assert denied.status_code == 403

    summary = client.get("/api/manager/summary?manager_email=manager@ibm.com").json()
    story = client.get("/api/story/resolved").json()

    assert summary["total_suggestions"] >= 4
    assert story["status"] == "Resolved"
    assert story["updates"]


def test_manager_page_serves_dashboard_gate(client):
    response = client.get("/manager")

    assert response.status_code == 200
    assert "Manager access" in response.text
    assert "managerDashboard" in response.text


def test_publish_draft_adds_public_suggestion(client, monkeypatch):
    from docs.examples.suggesthub.app import main

    monkeypatch.setattr(main, "get_bob_agent", lambda: FakeBob())
    draft = client.post(
        "/api/bob/message",
        json={"message": "Need more quiet rooms in Austin", "thread_id": "publish-test"},
    ).json()["draft"]

    published = client.post(
        "/api/bob/publish",
        json={"draft_id": draft["id"], "author_name": "Test User"},
    )

    assert published.status_code == 200
    assert published.json()["author_name"] == "Test User"
