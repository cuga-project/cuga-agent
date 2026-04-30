"""
Tests the CRM workflow: match a list of known contact emails against the CRM,
look up their accounts, compute revenue percentiles, and render an email template.

Mirrors the agent workflow without requiring live filesystem / MCP servers.
The fixture uses the real seed_database() — no manual inserts.
"""

import os
import sqlite3
import tempfile
import textwrap
import pytest
from fastapi.testclient import TestClient

from cuga.demo_tools.crm.crm_api.database import _DDL, get_db
from cuga.demo_tools.crm.crm_api.seed_data import seed_database
from cuga.demo_tools.crm.crm_api.main import app

EMAIL_TEMPLATE = textwrap.dedent("""\
    # Email Template

    ## Subject
    Account Performance Update - Q1 2026

    ## Body

    Hello team,

    I wanted to share a summary of our key accounts and their performance rankings.
    Below is an analysis of accounts by revenue percentile:

    <results>

    This data shows how each account ranks relative to our entire customer base.

    Best regards
""")

UNKNOWN_EMAILS = [
    "nobody@nowhere.com",
    "also@unknown.io",
    "fake.user@notareal.domain",
]


# ---------------------------------------------------------------------------
# Fixture: temp DB seeded with the real seed_database() data
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def workflow_client():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "workflow.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_DDL)
        conn.commit()
        seed_database(conn)

        def override_get_db():
            yield conn

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()
        conn.close()


# ---------------------------------------------------------------------------
# Helpers (pure-Python equivalents of the agent tool calls)
# ---------------------------------------------------------------------------
def fetch_all_contacts(client: TestClient) -> list[dict]:
    result, skip, limit = [], 0, 300
    while True:
        items = client.get("/contacts/", params={"skip": skip, "limit": limit}).json().get("items", [])
        result.extend(items)
        if len(items) < limit:
            break
        skip += limit
    return result


def fetch_all_accounts(client: TestClient) -> list[dict]:
    result, skip, limit = [], 0, 300
    while True:
        items = client.get("/accounts/", params={"skip": skip, "limit": limit}).json().get("items", [])
        result.extend(items)
        if len(items) < limit:
            break
        skip += limit
    return result


def revenue_percentile(value: float, sorted_revenues: list[float]) -> float:
    n = len(sorted_revenues)
    if n == 0:
        return 0.0
    return round(sum(1 for r in sorted_revenues if r <= value) / n * 100, 2)


def run_workflow(client: TestClient, contacts_txt: str, template: str) -> tuple[str, list[dict]]:
    email_list = [line.strip() for line in contacts_txt.splitlines() if line.strip()]

    all_contacts = fetch_all_contacts(client)
    email_to_contact = {c["email"]: c for c in all_contacts}
    matched_emails = [e for e in email_list if e in email_to_contact]

    account_details = {}
    for acc_id in {email_to_contact[e]["account_id"] for e in matched_emails}:
        account_details[acc_id] = client.get(f"/accounts/{acc_id}").json()

    revenues = sorted(
        a["annual_revenue"] for a in fetch_all_accounts(client) if a["annual_revenue"] is not None
    )

    entries = []
    for email in matched_emails:
        contact = email_to_contact[email]
        acc = account_details.get(contact["account_id"], {})
        rev = acc.get("annual_revenue")
        entries.append(
            {
                "contact_name": f"{contact['first_name']} {contact['last_name']}",
                "account_name": acc.get("name", "Unknown"),
                "revenue_percentile": revenue_percentile(rev, revenues) if rev is not None else "N/A",
            }
        )

    table = "| Contact | Account | Revenue Percentile |\n|---|---|---|\n"
    table += "\n".join(
        f"| {e['contact_name']} | {e['account_name']} | {e['revenue_percentile']}% |" for e in entries
    )
    return template.replace("<results>", table), entries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_crm_is_seeded(workflow_client):
    """Seed produced the expected number of records."""
    accounts = fetch_all_accounts(workflow_client)
    contacts = fetch_all_contacts(workflow_client)
    assert len(accounts) == 1000
    assert len(contacts) == 1000  # one contact per account


def test_unknown_emails_are_filtered_out(workflow_client):
    txt = "\n".join(UNKNOWN_EMAILS)
    _, entries = run_workflow(workflow_client, txt, EMAIL_TEMPLATE)
    assert entries == []


def test_empty_contacts_txt(workflow_client):
    final_email, entries = run_workflow(workflow_client, "", EMAIL_TEMPLATE)
    assert entries == []
    assert "| Contact |" in final_email  # table header still rendered
    assert "<results>" not in final_email


def test_workflow_with_real_crm_contacts(workflow_client):
    """Pick a few real contacts from the seeded CRM and run the full workflow."""
    all_contacts = fetch_all_contacts(workflow_client)
    # Use first 4 seeded contacts as if they came from contacts.txt
    sample = all_contacts[:4]
    contacts_txt = "\n".join(c["email"] for c in sample)

    _, entries = run_workflow(workflow_client, contacts_txt, EMAIL_TEMPLATE)
    assert len(entries) == 4


def test_unknown_emails_mixed_in(workflow_client):
    """Unknown emails in the list are silently dropped."""
    all_contacts = fetch_all_contacts(workflow_client)
    real_emails = [c["email"] for c in all_contacts[:3]]
    mixed = "\n".join(real_emails + UNKNOWN_EMAILS)

    _, entries = run_workflow(workflow_client, mixed, EMAIL_TEMPLATE)
    assert len(entries) == 3
    result_emails = {e["contact_name"] for e in entries}
    assert len(result_emails) == 3


def test_revenue_percentiles_are_valid(workflow_client):
    all_contacts = fetch_all_contacts(workflow_client)
    contacts_txt = "\n".join(c["email"] for c in all_contacts[:10])
    _, entries = run_workflow(workflow_client, contacts_txt, EMAIL_TEMPLATE)
    for entry in entries:
        perc = entry["revenue_percentile"]
        assert isinstance(perc, float)
        assert 0.0 <= perc <= 100.0, f"Out of range: {perc} for {entry['account_name']}"


def test_revenue_percentile_ordering(workflow_client):
    """An account with higher revenue should have a higher or equal percentile."""
    all_accounts = fetch_all_accounts(workflow_client)
    # Find the account with the highest and lowest revenue that have contacts
    all_contacts = fetch_all_contacts(workflow_client)
    contact_account_ids = {c["account_id"] for c in all_contacts}
    revenue_accounts = [a for a in all_accounts if a["id"] in contact_account_ids and a["annual_revenue"]]
    revenue_accounts.sort(key=lambda a: a["annual_revenue"])

    low_acc = revenue_accounts[0]
    high_acc = revenue_accounts[-1]

    low_contact = next(c for c in all_contacts if c["account_id"] == low_acc["id"])
    high_contact = next(c for c in all_contacts if c["account_id"] == high_acc["id"])

    contacts_txt = f"{low_contact['email']}\n{high_contact['email']}"
    _, entries = run_workflow(workflow_client, contacts_txt, EMAIL_TEMPLATE)

    by_account = {e["account_name"]: e["revenue_percentile"] for e in entries}
    assert by_account[high_acc["name"]] >= by_account[low_acc["name"]]


def test_output_contains_template_structure(workflow_client):
    all_contacts = fetch_all_contacts(workflow_client)
    contacts_txt = "\n".join(c["email"] for c in all_contacts[:2])
    final_email, _ = run_workflow(workflow_client, contacts_txt, EMAIL_TEMPLATE)
    assert "Account Performance Update" in final_email
    assert "<results>" not in final_email
    assert "| Contact | Account | Revenue Percentile |" in final_email


def test_output_contains_contact_names(workflow_client):
    all_contacts = fetch_all_contacts(workflow_client)
    sample = all_contacts[:3]
    contacts_txt = "\n".join(c["email"] for c in sample)
    final_email, _ = run_workflow(workflow_client, contacts_txt, EMAIL_TEMPLATE)
    for c in sample:
        full_name = f"{c['first_name']} {c['last_name']}"
        assert full_name in final_email, f"{full_name} missing from output"
