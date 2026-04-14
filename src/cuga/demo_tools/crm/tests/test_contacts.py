"""
Unit tests for Contacts API
Tests validation of specific contacts from contacts.txt and get contact by email functionality
All tests use API endpoints only (no direct database access)
"""

import pytest


# Test emails from contacts.txt (lines 1-4)
TEST_CONTACT_EMAILS = [
    "sarah.bell@gammadeltainc.partners.org",
    "sharon.jimenez@upsiloncorp.innovation.org",
    "ruth.ross@sigmasystems.operations.com",
    "dorothy.richardson@nextgencorp.gmail.com",
]


@pytest.fixture
def test_account(client):
    """Create a test account via API"""
    account_data = {
        "name": "Test Account",
        "industry": "Technology",
        "region": "North America",
        "annual_revenue": 100000.0,
    }
    response = client.post("/accounts/", json=account_data)
    assert response.status_code == 200, "Should create account successfully"
    return response.json()


@pytest.fixture
def test_contacts(client, test_account):
    """Create test contacts with emails from contacts.txt via API"""
    contacts = []
    first_names = ["Sarah", "Sharon", "Ruth", "Dorothy"]
    last_names = ["Bell", "Jimenez", "Ross", "Richardson"]
    job_titles = ["CEO", "CTO", "CFO", "VP Sales"]

    for i, email in enumerate(TEST_CONTACT_EMAILS):
        contact_data = {
            "first_name": first_names[i],
            "last_name": last_names[i],
            "email": email,
            "phone": f"+1-555-{1000 + i:04d}",
            "job_title": job_titles[i],
            "department": "Executive",
            "is_primary": (i == 0),
            "account_id": test_account["id"],
        }
        response = client.post("/contacts/", json=contact_data)
        assert response.status_code == 200, f"Should create contact {email} successfully"
        contacts.append(response.json())
    return contacts


def test_contacts_exist(client, test_contacts):
    """Test that all contacts from contacts.txt exist via API"""
    for email in TEST_CONTACT_EMAILS:
        response = client.get("/contacts/", params={"email": email})
        assert response.status_code == 200, f"Should get 200 OK for email {email}"

        data = response.json()
        assert "items" in data, "Response should contain 'items'"
        assert len(data["items"]) > 0, f"Should find at least one contact for email {email}"

        contact = data["items"][0]
        assert contact["email"] == email, f"Contact email should match {email}"


def test_get_contact_by_email_via_api(client, test_contacts):
    """Test getting contacts by email using the API endpoint"""
    for email in TEST_CONTACT_EMAILS:
        response = client.get("/contacts/", params={"email": email})
        assert response.status_code == 200, f"Should get 200 OK for email {email}"

        data = response.json()
        assert "items" in data, "Response should contain 'items'"
        assert len(data["items"]) > 0, f"Should find at least one contact for email {email}"

        contact = data["items"][0]
        assert contact["email"] == email, f"Contact email should match {email}"
        assert "id" in contact, "Contact should have an id"
        assert "first_name" in contact, "Contact should have a first_name"
        assert "last_name" in contact, "Contact should have a last_name"


def test_get_contact_by_email_exact_match(client, test_contacts):
    """Test that get contact by email returns exact match"""
    email = TEST_CONTACT_EMAILS[0]
    response = client.get("/contacts/", params={"email": email})

    assert response.status_code == 200
    data = response.json()

    assert data["total"] >= 1, f"Should find at least one contact for {email}"
    assert len(data["items"]) >= 1, f"Should return at least one contact for {email}"

    # Verify all returned contacts have the matching email
    for contact in data["items"]:
        assert contact["email"] == email, f"All returned contacts should have email {email}"


def test_get_contact_by_email_not_found(client, test_contacts):
    """Test that non-existent email returns empty results"""
    response = client.get("/contacts/", params={"email": "nonexistent@example.com"})

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 0, "Should find zero contacts for non-existent email"
    assert len(data["items"]) == 0, "Should return empty items list"


def test_all_test_contacts_have_required_fields(client, test_contacts):
    """Test that all test contacts have required fields"""
    for email in TEST_CONTACT_EMAILS:
        response = client.get("/contacts/", params={"email": email})
        assert response.status_code == 200

        data = response.json()
        contact = data["items"][0]

        required_fields = ["id", "email", "first_name", "last_name", "account_id"]
        for field in required_fields:
            assert field in contact, f"Contact should have {field} field"
            assert contact[field] is not None, f"Contact {field} should not be None"


def test_get_contact_by_id_via_api(client, test_contacts):
    """Test getting a contact by ID via API"""
    contact = test_contacts[0]
    contact_id = contact["id"]

    response = client.get(f"/contacts/{contact_id}")
    assert response.status_code == 200, "Should get 200 OK for contact ID"

    data = response.json()
    assert data["id"] == contact_id, "Returned contact should have matching ID"
    assert data["email"] == contact["email"], "Returned contact should have matching email"
    assert data["first_name"] == contact["first_name"], "Returned contact should have matching first_name"
    assert data["last_name"] == contact["last_name"], "Returned contact should have matching last_name"
