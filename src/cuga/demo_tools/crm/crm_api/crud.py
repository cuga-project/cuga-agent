import sqlite3
import math
from datetime import datetime, timezone
from typing import Optional

from .schemas import PaginatedResponse


def _row(cur: sqlite3.Cursor) -> Optional[dict]:
    row = cur.fetchone()
    return dict(row) if row else None


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class BaseCRUD:
    table: str
    columns: list[str]

    def create(self, conn: sqlite3.Connection, obj_in) -> dict:
        data = {k: v for k, v in obj_in.model_dump().items() if k in self.columns}
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        cur = conn.execute(
            f"INSERT INTO {self.table} ({cols}) VALUES ({placeholders})",
            list(data.values()),
        )
        conn.commit()
        return _row(conn.execute(f"SELECT * FROM {self.table} WHERE id = ?", (cur.lastrowid,)))

    def get(self, conn: sqlite3.Connection, id: int) -> Optional[dict]:
        return _row(conn.execute(f"SELECT * FROM {self.table} WHERE id = ?", (id,)))

    def get_paginated(self, conn: sqlite3.Connection, skip: int = 0, limit: int = 300) -> PaginatedResponse:
        total = conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]
        items = _rows(conn.execute(f"SELECT * FROM {self.table} LIMIT ? OFFSET ?", (limit, skip)))
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1
        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)

    def update(self, conn: sqlite3.Connection, id: int, obj_in) -> Optional[dict]:
        data = {k: v for k, v in obj_in.model_dump(exclude_unset=True).items() if k in self.columns}
        if not data:
            return self.get(conn, id)
        data["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in data)
        conn.execute(f"UPDATE {self.table} SET {sets} WHERE id = ?", [*data.values(), id])
        conn.commit()
        return self.get(conn, id)

    def delete(self, conn: sqlite3.Connection, id: int) -> Optional[dict]:
        if not self.get(conn, id):
            return None
        conn.execute(f"DELETE FROM {self.table} WHERE id = ?", (id,))
        conn.commit()
        return {"message": f"{self.table.rstrip('s').capitalize()} deleted successfully"}

    def count(self, conn: sqlite3.Connection) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]


class AccountCRUD(BaseCRUD):
    table = "accounts"
    columns = [
        "name",
        "industry",
        "website",
        "phone",
        "address",
        "city",
        "state",
        "country",
        "region",
        "annual_revenue",
        "employee_count",
    ]

    def get_paginated(
        self, conn: sqlite3.Connection, skip: int = 0, limit: int = 300, state: Optional[str] = None
    ) -> PaginatedResponse:
        where = "WHERE state = ?" if state else ""
        params = [state] if state else []
        total = conn.execute(f"SELECT COUNT(*) FROM {self.table} {where}", params).fetchone()[0]
        items = _rows(
            conn.execute(f"SELECT * FROM {self.table} {where} LIMIT ? OFFSET ?", [*params, limit, skip])
        )
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1
        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)


class LeadCRUD(BaseCRUD):
    table = "leads"
    columns = [
        "first_name",
        "last_name",
        "email",
        "phone",
        "company",
        "job_title",
        "industry",
        "source",
        "status",
        "score",
        "notes",
    ]


class ContactCRUD(BaseCRUD):
    table = "contacts"
    columns = [
        "first_name",
        "last_name",
        "email",
        "phone",
        "job_title",
        "department",
        "is_primary",
        "account_id",
    ]

    def get_paginated(
        self, conn: sqlite3.Connection, skip: int = 0, limit: int = 300, email: Optional[str] = None
    ) -> PaginatedResponse:
        where = "WHERE email = ?" if email else ""
        params = [email] if email else []
        total = conn.execute(f"SELECT COUNT(*) FROM {self.table} {where}", params).fetchone()[0]
        items = _rows(
            conn.execute(f"SELECT * FROM {self.table} {where} LIMIT ? OFFSET ?", [*params, limit, skip])
        )
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1
        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)


class OpportunityCRUD(BaseCRUD):
    table = "opportunities"
    columns = ["name", "description", "value", "currency", "stage", "probability", "close_date", "account_id"]

    def get_paginated(
        self, conn: sqlite3.Connection, skip: int = 0, limit: int = 300, account_id: Optional[int] = None
    ) -> PaginatedResponse:
        where = "WHERE account_id = ?" if account_id is not None else ""
        params = [account_id] if account_id is not None else []
        total = conn.execute(f"SELECT COUNT(*) FROM {self.table} {where}", params).fetchone()[0]
        items = _rows(
            conn.execute(f"SELECT * FROM {self.table} {where} LIMIT ? OFFSET ?", [*params, limit, skip])
        )
        pages = math.ceil(total / limit) if limit > 0 else 0
        page = (skip // limit) + 1 if limit > 0 else 1
        return PaginatedResponse(items=items, total=total, page=page, pages=pages, per_page=limit)


account_crud = AccountCRUD()
lead_crud = LeadCRUD()
contact_crud = ContactCRUD()
opportunity_crud = OpportunityCRUD()
