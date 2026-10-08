"""SQLite sales book for the embedded SDK demo."""

from __future__ import annotations

import sqlite3
from pathlib import Path

STAGES = (
    "prospecting",
    "qualification",
    "proposal",
    "negotiation",
    "closed-won",
    "closed-lost",
)

_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    industry TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    annual_revenue REAL NOT NULL,
    owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    company TEXT NOT NULL,
    email TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    score INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    name TEXT NOT NULL,
    value REAL NOT NULL,
    stage TEXT NOT NULL,
    probability INTEGER NOT NULL,
    close_date TEXT NOT NULL
);
"""


class SalesStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
            if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
                self._seed(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _seed(self, conn: sqlite3.Connection) -> None:
        accounts = [
            ("Northwind Analytics", "Technology", "Boston", "Northeast", 42000000, "Maya Chen"),
            ("Lumen Health", "Healthcare", "Austin", "South", 31000000, "Andre Walsh"),
            ("Brightline Finance", "Finance", "Chicago", "Midwest", 67000000, "Maya Chen"),
            ("Kepler Manufacturing", "Manufacturing", "Detroit", "Midwest", 18000000, "Priya Shah"),
            ("Paper & Pine", "Retail", "Portland", "West", 9000000, "Andre Walsh"),
            ("Aster Schools", "Education", "Denver", "West", 6000000, "Priya Shah"),
        ]
        conn.executemany(
            "INSERT INTO accounts (name, industry, city, region, annual_revenue, owner) VALUES (?, ?, ?, ?, ?, ?)",
            accounts,
        )
        by_name = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM accounts")}
        contacts = [
            (by_name["Northwind Analytics"], "Helen", "Cho", "helen.cho@northwind.example", "VP Engineering"),
            (by_name["Northwind Analytics"], "Owen", "Blake", "owen.blake@northwind.example", "Procurement"),
            (by_name["Lumen Health"], "Ruth", "Adler", "ruth.adler@lumen.example", "CTO"),
            (
                by_name["Brightline Finance"],
                "Samir",
                "Patel",
                "samir.patel@brightline.example",
                "Head of Risk",
            ),
            (by_name["Kepler Manufacturing"], "Nora", "Feld", "nora.feld@kepler.example", "Plant Director"),
            (by_name["Paper & Pine"], "Eli", "Brooks", "eli.brooks@paperpine.example", "CEO"),
            (by_name["Aster Schools"], "June", "Hart", "june.hart@aster.example", "COO"),
        ]
        conn.executemany(
            "INSERT INTO contacts (account_id, first_name, last_name, email, title) VALUES (?, ?, ?, ?, ?)",
            contacts,
        )
        leads = [
            ("Imani", "Ross", "Cedar Logistics", "imani.ross@cedar.example", "Trade show", "new", 72),
            ("Theo", "Marin", "Halcyon Bio", "theo.marin@halcyon.example", "Referral", "working", 81),
            (
                "Greta",
                "Nguyen",
                "Fieldnote Media",
                "greta.nguyen@fieldnote.example",
                "Website",
                "qualified",
                90,
            ),
            ("Paul", "Okoye", "Sable Insurance", "paul.okoye@sable.example", "Partner", "new", 64),
            ("Leah", "Voss", "Kinfolk Foods", "leah.voss@kinfolk.example", "Email campaign", "working", 58),
            ("Chris", "Dalton", "Orbit Transit", "chris.dalton@orbit.example", "LinkedIn", "new", 47),
        ]
        conn.executemany(
            "INSERT INTO leads (first_name, last_name, company, email, source, status, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            leads,
        )
        opportunities = [
            (by_name["Northwind Analytics"], "Platform renewal", 180000, "negotiation", 70, "2026-10-28"),
            (by_name["Northwind Analytics"], "Data residency add-on", 128000, "proposal", 40, "2026-11-12"),
            (by_name["Lumen Health"], "Patient portal", 240000, "proposal", 55, "2026-11-04"),
            (by_name["Lumen Health"], "Claims automation", 205000, "qualification", 25, "2026-12-01"),
            (by_name["Brightline Finance"], "Risk desk rollout", 96000, "qualification", 30, "2026-11-18"),
            (by_name["Kepler Manufacturing"], "Line sensors", 310000, "prospecting", 15, "2026-12-15"),
            (by_name["Paper & Pine"], "Loyalty rebuild", 72000, "negotiation", 65, "2026-10-22"),
            (by_name["Aster Schools"], "Campus network", 54000, "closed-won", 100, "2026-09-30"),
        ]
        conn.executemany(
            "INSERT INTO opportunities (account_id, name, value, stage, probability, close_date) VALUES (?, ?, ?, ?, ?, ?)",
            opportunities,
        )
        conn.commit()

    def list_accounts(self, industry: str | None = None) -> list[dict]:
        query = "SELECT * FROM accounts"
        params: list[str] = []
        if industry:
            query += " WHERE industry = ? COLLATE NOCASE"
            params.append(industry)
        query += " ORDER BY name"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def get_account(self, name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if row is None:
                return None
            account = dict(row)
            account["contacts"] = [
                dict(item)
                for item in conn.execute(
                    "SELECT first_name, last_name, email, title FROM contacts WHERE account_id = ? ORDER BY last_name",
                    (account["id"],),
                )
            ]
            account["opportunities"] = [
                dict(item)
                for item in conn.execute(
                    "SELECT id, name, value, stage, probability, close_date FROM opportunities WHERE account_id = ? ORDER BY value DESC",
                    (account["id"],),
                )
            ]
            return account

    def list_contacts(self) -> list[dict]:
        query = """
            SELECT contacts.*, accounts.name AS account_name
            FROM contacts JOIN accounts ON accounts.id = contacts.account_id
            ORDER BY accounts.name, contacts.last_name
        """
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query)]

    def list_leads(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM leads"
        params: list[str] = []
        if status:
            query += " WHERE status = ? COLLATE NOCASE"
            params.append(status)
        query += " ORDER BY score DESC"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def list_opportunities(self, stage: str | None = None) -> list[dict]:
        query = """
            SELECT opportunities.*, accounts.name AS account_name, accounts.owner AS owner
            FROM opportunities JOIN accounts ON accounts.id = opportunities.account_id
        """
        params: list[str] = []
        if stage:
            query += " WHERE opportunities.stage = ? COLLATE NOCASE"
            params.append(stage)
        query += " ORDER BY opportunities.value DESC"
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    def update_opportunity_stage(self, opportunity_id: int, stage: str) -> dict:
        if stage not in STAGES:
            raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE opportunities SET stage = ? WHERE id = ?",
                (stage, opportunity_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"opportunity {opportunity_id} was not found")
            conn.commit()
        matches = [row for row in self.list_opportunities() if row["id"] == opportunity_id]
        return matches[0]

    def summary(self) -> dict:
        opportunities = self.list_opportunities()
        open_deals = [row for row in opportunities if row["stage"] not in ("closed-won", "closed-lost")]
        won = [row for row in opportunities if row["stage"] == "closed-won"]
        lost = [row for row in opportunities if row["stage"] == "closed-lost"]
        decided = len(won) + len(lost)
        return {
            "account_count": len(self.list_accounts()),
            "lead_count": len(self.list_leads()),
            "contact_count": len(self.list_contacts()),
            "open_deal_count": len(open_deals),
            "open_pipeline": sum(row["value"] for row in open_deals),
            "won_value": sum(row["value"] for row in won),
            "win_rate": (len(won) / decided) if decided else 0.0,
            "stages": list(STAGES),
        }
