"""
Tests for the Contacts API endpoint.
"""

import pytest

TEST_CONTACT_EMAILS = [
    "john.doe@testcorp.com",
    "jane.smith@testcorp.com",
    "bob.johnson@anothercorp.com",
]


@pytest.fixture
def seeded_client(client):
    """Client guaranteed to have the minimal seed contacts."""
    return client


def test_contacts_exist(seeded_client):
    for email in TEST_CONTACT_EMAILS:
        resp = seeded_client.get("/contacts/", params={"email": email})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) > 0, f"No contact found for {email}"
        assert data["items"][0]["email"] == email


def test_get_contact_by_email_exact_match(seeded_client):
    email = TEST_CONTACT_EMAILS[0]
    resp = seeded_client.get("/contacts/", params={"email": email})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    for contact in data["items"]:
        assert contact["email"] == email


def test_get_contact_by_email_not_found(seeded_client):
    resp = seeded_client.get("/contacts/", params={"email": "nobody@nowhere.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_contacts_have_required_fields(seeded_client):
    for email in TEST_CONTACT_EMAILS:
        resp = seeded_client.get("/contacts/", params={"email": email})
        contact = resp.json()["items"][0]
        for field in ("id", "email", "first_name", "last_name", "account_id"):
            assert field in contact
            assert contact[field] is not None


def test_get_contact_by_id(seeded_client):
    resp = seeded_client.get("/contacts/", params={"email": TEST_CONTACT_EMAILS[0]})
    contact = resp.json()["items"][0]
    by_id = seeded_client.get(f"/contacts/{contact['id']}")
    assert by_id.status_code == 200
    assert by_id.json()["email"] == contact["email"]


def test_create_and_delete_contact(seeded_client, sample_data):
    acc_id = sample_data["accounts"][0]["id"]
    payload = {
        "first_name": "New",
        "last_name": "Person",
        "email": "new.person@example.com",
        "account_id": acc_id,
    }
    create = seeded_client.post("/contacts/", json=payload)
    assert create.status_code == 200
    cid = create.json()["id"]

    delete = seeded_client.delete(f"/contacts/{cid}")
    assert delete.status_code == 200

    gone = seeded_client.get(f"/contacts/{cid}")
    assert gone.status_code == 404
