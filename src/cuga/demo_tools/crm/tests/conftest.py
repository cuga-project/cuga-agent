"""
Pytest configuration and fixtures for e2e tests
"""

import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from crm_api.database import Base, get_db
from crm_api.main import app
from crm_api.models import Account, Contact, Opportunity


def create_sample_data(db):
    """Create sample test data for API tests"""
    # Create test accounts
    account1 = Account(
        name="Test Corp", industry="Technology", region="North America", annual_revenue=50000.0
    )
    account2 = Account(name="Another Corp", industry="Finance", region="Europe", annual_revenue=20000.0)

    db.add(account1)
    db.add(account2)
    db.commit()
    db.refresh(account1)
    db.refresh(account2)

    # Create test opportunities
    opp1 = Opportunity(
        name="Big Deal", value=25000.0, probability=0.7, stage="proposal", account_id=account1.id
    )
    opp2 = Opportunity(
        name="Small Deal", value=5000.0, probability=0.3, stage="prospecting", account_id=account1.id
    )
    opp3 = Opportunity(
        name="Medium Deal", value=15000.0, probability=0.6, stage="negotiation", account_id=account2.id
    )

    db.add(opp1)
    db.add(opp2)
    db.add(opp3)
    db.commit()
    db.refresh(opp1)
    db.refresh(opp2)
    db.refresh(opp3)

    # Create test contacts
    contact1 = Contact(
        first_name="John",
        last_name="Doe",
        email="john.doe@testcorp.com",
        job_title="CEO",
        department="Executive",
        account_id=account1.id,
        is_primary=True,
    )
    contact2 = Contact(
        first_name="Jane",
        last_name="Smith",
        email="jane.smith@testcorp.com",
        job_title="CTO",
        department="Technology",
        account_id=account1.id,
        is_primary=False,
    )
    contact3 = Contact(
        first_name="Bob",
        last_name="Johnson",
        email="bob.johnson@anothercorp.com",
        job_title="CFO",
        department="Finance",
        account_id=account2.id,
        is_primary=True,
    )

    db.add(contact1)
    db.add(contact2)
    db.add(contact3)
    db.commit()
    db.refresh(contact1)
    db.refresh(contact2)
    db.refresh(contact3)

    return {
        'accounts': [account1, account2],
        'opportunities': [opp1, opp2, opp3],
        'contacts': [contact1, contact2, contact3],
    }


@pytest.fixture(scope="session")
def test_db():
    """Create a test database for e2e tests"""
    # Use SQLite in-memory database for tests
    test_db_url = "sqlite:///./test_crm.db"

    # Create test database engine
    engine = create_engine(test_db_url, connect_args={"check_same_thread": False})

    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Create session factory
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    try:
        yield TestingSessionLocal

    finally:
        # Clean up
        if os.path.exists("./test_crm.db"):
            os.unlink("./test_crm.db")


@pytest.fixture
def db_session(test_db):
    """Provide a database session for each test"""
    session = test_db()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(test_db):
    """Create a test client with real database"""

    def override_get_db():
        session = test_db()
        try:
            yield session
        finally:
            session.close()

    # Override the get_db dependency
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    # Clean up overrides
    app.dependency_overrides.clear()


@pytest.fixture
def sample_data(db_session):
    """Create sample test data for specific test scenarios"""
    return create_sample_data(db_session)


@pytest.fixture
def api_db_session(test_db):
    """Database session fixture for API tests that ensures sample data exists"""
    session = test_db()
    try:
        # Ensure sample data exists
        if session.query(Account).count() == 0:
            create_sample_data(session)
        yield session
    finally:
        session.close()
