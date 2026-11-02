"""Small durable ACP session directory and text transcript for DAM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class StoredSession:
    session_id: str
    context_id: str
    cwd: str
    additional_directories: tuple[str, ...]
    created_at: str
    updated_at: str | None


class AcpSessionStore:
    """Persist identifiers and delivered text without serializing model credentials."""

    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                context_id TEXT NOT NULL,
                cwd TEXT NOT NULL,
                additional_directories TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_by_session
                ON messages(session_id, sequence);
            """
        )

    @staticmethod
    def _session(row: tuple) -> StoredSession:
        return StoredSession(
            session_id=row[0],
            context_id=row[1],
            cwd=row[2],
            additional_directories=tuple(json.loads(row[3])),
            created_at=row[4],
            updated_at=row[5],
        )

    def add(
        self,
        session_id: str,
        context_id: str,
        cwd: str,
        additional_directories: tuple[str, ...],
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, NULL)",
                (session_id, context_id, cwd, json.dumps(additional_directories), _now()),
            )

    def get(self, session_id: str) -> StoredSession | None:
        row = self._db.execute(
            "SELECT session_id, context_id, cwd, additional_directories, created_at, updated_at "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return self._session(row) if row is not None else None

    def list(self) -> list[StoredSession]:
        rows = self._db.execute(
            "SELECT session_id, context_id, cwd, additional_directories, created_at, updated_at "
            "FROM sessions ORDER BY COALESCE(updated_at, created_at) DESC, session_id"
        ).fetchall()
        return [self._session(row) for row in rows]

    def append(self, session_id: str, role: str, text: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("unsupported transcript role")
        with self._db:
            self._db.execute(
                "INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
                (session_id, role, text),
            )
            self._db.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (_now(), session_id))

    def messages(self, session_id: str) -> list[tuple[str, str]]:
        return self._db.execute(
            "SELECT role, text FROM messages WHERE session_id = ? ORDER BY sequence", (session_id,)
        ).fetchall()

    def close(self) -> None:
        self._db.close()
