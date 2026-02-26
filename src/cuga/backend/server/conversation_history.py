"""
Conversation History Persistence Module

This module provides functionality to persist conversation history to a database.
Each conversation is stored with multiple keys: agent_id, thread_id, version, and user_id.
Uses the storage layer (get_storage().get_relational_store("conversation")) for local SQLite or prod Postgres.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id


class ConversationMessage(BaseModel):
    role: str
    content: str
    timestamp: str
    metadata: Optional[Dict[str, Any]] = None


class StreamEvent(BaseModel):
    event_name: str
    event_data: str
    timestamp: str
    sequence: int


class ConversationHistory(BaseModel):
    agent_id: str
    thread_id: str
    version: int
    user_id: str
    messages: List[ConversationMessage]
    created_at: str
    updated_at: str


class ConversationStreamHistory(BaseModel):
    agent_id: str
    thread_id: str
    user_id: str
    events: List[StreamEvent]
    created_at: str
    updated_at: str


def _instance_id() -> str:
    return get_service_instance_id()


def _tenant_id() -> str:
    return get_tenant_id()


class ConversationHistoryDB:
    def __init__(self, db_path: Optional[str] = None):
        self._schema_ensured = False

    def _get_store(self):
        return get_storage().get_relational_store("conversation")

    async def _ensure_schema(self):
        if self._schema_ensured:
            return
        store = self._get_store()
        try:
            await store.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    tenant_id TEXT NOT NULL DEFAULT '',
                    instance_id TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, instance_id, agent_id, thread_id, version, user_id)
                )
            """)
            await store.execute("""
                CREATE TABLE IF NOT EXISTS stream_events (
                    tenant_id TEXT NOT NULL DEFAULT '',
                    instance_id TEXT NOT NULL DEFAULT '',
                    agent_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    events TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, instance_id, agent_id, thread_id, user_id)
                )
            """)
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_thread_id ON conversation_history(thread_id)",
                "CREATE INDEX IF NOT EXISTS idx_user_id ON conversation_history(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_agent_id ON conversation_history(agent_id)",
                "CREATE INDEX IF NOT EXISTS idx_updated_at ON conversation_history(updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_stream_thread_id ON stream_events(thread_id)",
                "CREATE INDEX IF NOT EXISTS idx_stream_user_id ON stream_events(user_id)",
            ]:
                await store.execute(idx_sql)
            await store.commit()
            self._schema_ensured = True
        finally:
            await store.close()

    async def save_conversation(
        self, agent_id: str, thread_id: str, version: int, user_id: str, messages: List[Dict[str, Any]]
    ) -> bool:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                now = datetime.utcnow().isoformat()
                messages_json = json.dumps(messages)
                existing = await store.fetchone(
                    """
                    SELECT created_at FROM conversation_history
                    WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                    """,
                    (tenant_id, inst_id, agent_id, thread_id, version, user_id),
                )
                if existing:
                    await store.execute(
                        """
                        UPDATE conversation_history
                        SET messages = ?, updated_at = ?
                        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                        """,
                        (messages_json, now, tenant_id, inst_id, agent_id, thread_id, version, user_id),
                    )
                else:
                    await store.execute(
                        """
                        INSERT INTO conversation_history
                        (tenant_id, instance_id, agent_id, thread_id, version, user_id, messages, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (tenant_id, inst_id, agent_id, thread_id, version, user_id, messages_json, now, now),
                    )
                await store.commit()
                return True
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error saving conversation: {e}")
            return False

    async def get_conversation(
        self, agent_id: str, thread_id: str, version: int, user_id: str
    ) -> Optional[ConversationHistory]:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                row = await store.fetchone(
                    """
                    SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                    FROM conversation_history
                    WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                    """,
                    (tenant_id, inst_id, agent_id, thread_id, version, user_id),
                )
                if row:
                    return ConversationHistory(
                        agent_id=row["agent_id"],
                        thread_id=row["thread_id"],
                        version=row["version"],
                        user_id=row["user_id"],
                        messages=json.loads(row["messages"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                return None
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error retrieving conversation: {e}")
            return None

    async def get_thread_history(
        self, thread_id: str, user_id: Optional[str] = None
    ) -> List[ConversationHistory]:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                if user_id:
                    rows = await store.fetchall(
                        """
                        SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                        FROM conversation_history
                        WHERE tenant_id = ? AND instance_id = ? AND thread_id = ? AND user_id = ?
                        ORDER BY version DESC
                        """,
                        (tenant_id, inst_id, thread_id, user_id),
                    )
                else:
                    rows = await store.fetchall(
                        """
                        SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                        FROM conversation_history
                        WHERE tenant_id = ? AND instance_id = ? AND thread_id = ?
                        ORDER BY version DESC
                        """,
                        (tenant_id, inst_id, thread_id),
                    )
                return [
                    ConversationHistory(
                        agent_id=row["agent_id"],
                        thread_id=row["thread_id"],
                        version=row["version"],
                        user_id=row["user_id"],
                        messages=json.loads(row["messages"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                    for row in rows
                ]
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error retrieving thread history: {e}")
            return []

    async def get_latest_version(self, agent_id: str, thread_id: str, user_id: str) -> int:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                result = await store.fetchone(
                    """
                    SELECT MAX(version) as max FROM conversation_history
                    WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?
                    """,
                    (tenant_id, inst_id, agent_id, thread_id, user_id),
                )
                v = result.get("max") if result else None
                return v if v is not None else 0
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error getting latest version: {e}")
            return 0

    async def delete_conversation(self, agent_id: str, thread_id: str, version: int, user_id: str) -> bool:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                await store.execute(
                    """
                    DELETE FROM conversation_history
                    WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                    """,
                    (tenant_id, inst_id, agent_id, thread_id, version, user_id),
                )
                await store.commit()
                return True
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error deleting conversation: {e}")
            return False

    async def delete_stream_events(self, agent_id: str, thread_id: str, user_id: str) -> bool:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                await store.execute(
                    "DELETE FROM stream_events WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?",
                    (tenant_id, inst_id, agent_id, thread_id, user_id),
                )
                await store.commit()
                return True
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error deleting stream events: {e}")
            return False

    async def delete_thread(self, agent_id: str, thread_id: str, user_id: str) -> bool:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                await store.execute(
                    "DELETE FROM conversation_history WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?",
                    (tenant_id, inst_id, agent_id, thread_id, user_id),
                )
                await store.execute(
                    "DELETE FROM stream_events WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?",
                    (tenant_id, inst_id, agent_id, thread_id, user_id),
                )
                await store.commit()
                return True
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error deleting thread: {e}")
            return False

    async def get_all_threads_for_agent(self, agent_id: str, user_id: str) -> List[Dict[str, Any]]:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                rows = await store.fetchall(
                    """
                    SELECT thread_id, MAX(version) as latest_version, MAX(updated_at) as updated_at
                    FROM conversation_history
                    WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND user_id = ?
                    GROUP BY thread_id
                    ORDER BY updated_at DESC
                    """,
                    (tenant_id, inst_id, agent_id, user_id),
                )
                threads = []
                for row in rows:
                    thread_id = row["thread_id"]
                    latest_version = row["latest_version"]
                    updated_at = row["updated_at"]
                    messages_row = await store.fetchone(
                        """
                        SELECT messages FROM conversation_history
                        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                        """,
                        (tenant_id, inst_id, agent_id, thread_id, latest_version, user_id),
                    )
                    first_message = "New Conversation"
                    if messages_row:
                        messages = json.loads(messages_row["messages"])
                        for msg in messages:
                            role = msg.get("role", "").lower()
                            if role in ("user", "human"):
                                content = msg.get("content", "")
                                if content and content.strip():
                                    first_message = content[:60] + "..." if len(content) > 60 else content
                                    break
                    if first_message == "New Conversation":
                        stream_row = await store.fetchone(
                            """
                            SELECT events FROM stream_events
                            WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?
                            """,
                            (tenant_id, inst_id, agent_id, thread_id, user_id),
                        )
                        if stream_row:
                            events = json.loads(stream_row["events"])
                            for event in events:
                                if event.get("event_name") == "UserMessage":
                                    event_data = event.get("event_data", "")
                                    if event_data and event_data.strip():
                                        first_message = (
                                            event_data[:60] + "..." if len(event_data) > 60 else event_data
                                        )
                                        break
                    threads.append(
                        {
                            "thread_id": thread_id,
                            "latest_version": latest_version,
                            "first_message": first_message,
                            "updated_at": updated_at,
                        }
                    )
                return threads
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error getting threads for agent: {e}")
            return []

    async def save_stream_events(
        self, agent_id: str, thread_id: str, user_id: str, events: List[Dict[str, Any]]
    ) -> bool:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                now = datetime.utcnow().isoformat()
                events_json = json.dumps(events)
                existing = await store.fetchone(
                    "SELECT created_at FROM stream_events WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?",
                    (tenant_id, inst_id, agent_id, thread_id, user_id),
                )
                if existing:
                    row = await store.fetchone(
                        "SELECT events FROM stream_events WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?",
                        (tenant_id, inst_id, agent_id, thread_id, user_id),
                    )
                    existing_events = json.loads(row["events"]) if row and row["events"] else []
                    combined_events = existing_events + events
                    await store.execute(
                        """
                        UPDATE stream_events SET events = ?, updated_at = ?
                        WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?
                        """,
                        (json.dumps(combined_events), now, tenant_id, inst_id, agent_id, thread_id, user_id),
                    )
                else:
                    await store.execute(
                        """
                        INSERT INTO stream_events (tenant_id, instance_id, agent_id, thread_id, user_id, events, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (tenant_id, inst_id, agent_id, thread_id, user_id, events_json, now, now),
                    )
                await store.commit()
                return True
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error saving stream events: {e}")
            return False

    async def get_stream_events(
        self, agent_id: str, thread_id: str, user_id: str
    ) -> Optional[ConversationStreamHistory]:
        try:
            await self._ensure_schema()
            store = self._get_store()
            tenant_id = _tenant_id()
            inst_id = _instance_id()
            try:
                row = await store.fetchone(
                    """
                    SELECT agent_id, thread_id, user_id, events, created_at, updated_at
                    FROM stream_events WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND thread_id = ? AND user_id = ?
                    """,
                    (tenant_id, inst_id, agent_id, thread_id, user_id),
                )
                if row:
                    return ConversationStreamHistory(
                        agent_id=row["agent_id"],
                        thread_id=row["thread_id"],
                        user_id=row["user_id"],
                        events=json.loads(row["events"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                return None
            finally:
                await store.close()
        except Exception as e:
            logger.error(f"Error retrieving stream events: {e}")
            return None

    async def append_stream_event(
        self, agent_id: str, thread_id: str, user_id: str, event_name: str, event_data: str, sequence: int
    ) -> bool:
        try:
            stream_history = await self.get_stream_events(agent_id, thread_id, user_id)
            new_event = {
                "event_name": event_name,
                "event_data": event_data,
                "timestamp": datetime.utcnow().isoformat(),
                "sequence": sequence,
            }
            events_list: List[Dict[str, Any]]
            if stream_history:
                events_list = [
                    event.model_dump() if hasattr(event, "model_dump") else dict(event)
                    for event in stream_history.events
                ]
                events_list.append(new_event)
            else:
                events_list = [new_event]
            return await self.save_stream_events(agent_id, thread_id, user_id, events_list)
        except Exception as e:
            logger.error(f"Error appending stream event: {e}")
            return False


_conversation_db: Optional[ConversationHistoryDB] = None


def get_conversation_db() -> ConversationHistoryDB:
    global _conversation_db
    if _conversation_db is None:
        _conversation_db = ConversationHistoryDB()
    return _conversation_db
