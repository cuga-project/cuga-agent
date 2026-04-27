import sqlite3
import pytest
from fastapi.testclient import TestClient

from cuga.demo_tools.crm.crm_api.database import get_db, _DDL
from cuga.demo_tools.crm.crm_api.main import app


def _make_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn


def _seed_minimal(conn: sqlite3.Connection) -> dict:
    cur = conn.execute(
        "INSERT INTO accounts (name, industry, region, annual_revenue) VALUES (?,?,?,?)",
        ("Test Corp", "Technology", "North America", 50000.0),
    )
    acc1_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO accounts (name, industry, region, annual_revenue) VALUES (?,?,?,?)",
        ("Another Corp", "Finance", "Europe", 20000.0),
    )
    acc2_id = cur.lastrowid

    conn.executemany(
        "INSERT INTO opportunities (name, value, probability, stage, account_id) VALUES (?,?,?,?,?)",
        [
            ("Big Deal", 25000.0, 0.7, "proposal", acc1_id),
            ("Small Deal", 5000.0, 0.3, "prospecting", acc1_id),
            ("Medium Deal", 15000.0, 0.6, "negotiation", acc2_id),
        ],
    )

    conn.executemany(
        "INSERT INTO contacts (first_name, last_name, email, job_title, department, is_primary, account_id) VALUES (?,?,?,?,?,?,?)",
        [
            ("John", "Doe", "john.doe@testcorp.com", "CEO", "Executive", 1, acc1_id),
            ("Jane", "Smith", "jane.smith@testcorp.com", "CTO", "Technology", 0, acc1_id),
            ("Bob", "Johnson", "bob.johnson@anothercorp.com", "CFO", "Finance", 1, acc2_id),
        ],
    )
    conn.commit()

    accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts").fetchall()]
    contacts = [dict(r) for r in conn.execute("SELECT * FROM contacts").fetchall()]
    opportunities = [dict(r) for r in conn.execute("SELECT * FROM opportunities").fetchall()]
    return {"accounts": accounts, "contacts": contacts, "opportunities": opportunities}


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return str(tmp_path_factory.mktemp("crm") / "test.db")


@pytest.fixture(scope="session")
def seeded_conn(db_path):
    conn = _make_conn(db_path)
    _seed_minimal(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db_path):
    conn = _make_conn(db_path)
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        _seed_minimal(conn)

    def override_get_db():
        yield conn

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    conn.close()


@pytest.fixture
def sample_data(seeded_conn):
    accounts = [dict(r) for r in seeded_conn.execute("SELECT * FROM accounts").fetchall()]
    contacts = [dict(r) for r in seeded_conn.execute("SELECT * FROM contacts").fetchall()]
    opportunities = [dict(r) for r in seeded_conn.execute("SELECT * FROM opportunities").fetchall()]
    return {"accounts": accounts, "contacts": contacts, "opportunities": opportunities}
