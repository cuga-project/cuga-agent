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


class ConversationMessage(BaseModel):
    """Model for a single conversation message"""

    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: str
    metadata: Optional[Dict[str, Any]] = None


class StreamEvent(BaseModel):
    """Model for a streaming event"""

    event_name: str  # 'CodeAgent', 'Thinking', 'Answer', etc.
    event_data: str  # The raw event data
    timestamp: str
    sequence: int  # Order of events in the conversation


class ConversationHistory(BaseModel):
    """Model for conversation history entry"""

    agent_id: str
    thread_id: str
    version: int
    user_id: str
    messages: List[ConversationMessage]
    created_at: str
    updated_at: str


class ConversationStreamHistory(BaseModel):
    """Model for conversation stream events history"""

    agent_id: str
    thread_id: str
    user_id: str
    events: List[StreamEvent]
    created_at: str
    updated_at: str


class ConversationHistoryDB:
    """Database manager for conversation history persistence"""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the conversation history database.
        Uses storage layer (cuga.db locally or Postgres in prod). db_path is ignored.
        """
        self._ensure_schema()
        logger.info("Conversation history database initialized (storage layer)")

    def _get_store(self):
        return get_storage().get_relational_store("conversation")

    def _ensure_schema(self):
        store = self._get_store()
        try:
            store.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    agent_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, thread_id, version, user_id)
                )
            """)
            store.execute("""
                CREATE TABLE IF NOT EXISTS stream_events (
                    agent_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    events TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, thread_id, user_id)
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
                store.execute(idx_sql)
            store.commit()
        finally:
            store.close()
        logger.info("Conversation history database tables created/verified")

    def save_conversation(
        self, agent_id: str, thread_id: str, version: int, user_id: str, messages: List[Dict[str, Any]]
    ) -> bool:
        try:
            store = self._get_store()
            try:
                now = datetime.utcnow().isoformat()
                messages_json = json.dumps(messages)
                existing = store.fetchone(
                    """
                    SELECT created_at FROM conversation_history
                    WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                    """,
                    (agent_id, thread_id, version, user_id),
                )
                if existing:
                    store.execute(
                        """
                        UPDATE conversation_history
                        SET messages = ?, updated_at = ?
                        WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                        """,
                        (messages_json, now, agent_id, thread_id, version, user_id),
                    )
                else:
                    store.execute(
                        """
                        INSERT INTO conversation_history
                        (agent_id, thread_id, version, user_id, messages, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (agent_id, thread_id, version, user_id, messages_json, now, now),
                    )
                store.commit()
                return True
            finally:
                store.close()
        except Exception as e:
            logger.error(f"Error saving conversation: {e}")
            return False

    def get_conversation(
        self, agent_id: str, thread_id: str, version: int, user_id: str
    ) -> Optional[ConversationHistory]:
        try:
            store = self._get_store()
            try:
                row = store.fetchone(
                    """
                    SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                    FROM conversation_history
                    WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                    """,
                    (agent_id, thread_id, version, user_id),
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
                store.close()
        except Exception as e:
            logger.error(f"Error retrieving conversation: {e}")
            return None

    def get_thread_history(self, thread_id: str, user_id: Optional[str] = None) -> List[ConversationHistory]:
        try:
            store = self._get_store()
            try:
                if user_id:
                    rows = store.fetchall(
                        """
                        SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                        FROM conversation_history
                        WHERE thread_id = ? AND user_id = ?
                        ORDER BY version DESC
                        """,
                        (thread_id, user_id),
                    )
                else:
                    rows = store.fetchall(
                        """
                        SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                        FROM conversation_history
                        WHERE thread_id = ?
                        ORDER BY version DESC
                        """,
                        (thread_id,),
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
                store.close()
        except Exception as e:
            logger.error(f"Error retrieving thread history: {e}")
            return []

    def get_latest_version(self, agent_id: str, thread_id: str, user_id: str) -> int:
        try:
            store = self._get_store()
            try:
                result = store.fetchone(
                    """
                    SELECT MAX(version) FROM conversation_history
                    WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                    """,
                    (agent_id, thread_id, user_id),
                )
                v = (
                    (result.get("max") if hasattr(result, "get") else (result[0] if result else None))
                    if result
                    else None
                )
                return v if v is not None else 0
            finally:
                store.close()
        except Exception as e:
            logger.error(f"Error getting latest version: {e}")
            return 0

    def delete_conversation(self, agent_id: str, thread_id: str, version: int, user_id: str) -> bool:
        try:
            store = self._get_store()
            try:
                store.execute(
                    """
                    DELETE FROM conversation_history
                    WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                    """,
                    (agent_id, thread_id, version, user_id),
                )
                store.commit()
                return True
            finally:
                store.close()
        except Exception as e:
            logger.error(f"Error deleting conversation: {e}")
            return False

    def delete_stream_events(self, agent_id: str, thread_id: str, user_id: str) -> bool:
        try:
            store = self._get_store()
            try:
                store.execute(
                    "DELETE FROM stream_events WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
                    (agent_id, thread_id, user_id),
                )
                store.commit()
                return True
            finally:
                store.close()
        except Exception as e:
            logger.error(f"Error deleting stream events: {e}")
            return False

    def delete_thread(self, agent_id: str, thread_id: str, user_id: str) -> bool:
        try:
            store = self._get_store()
            try:
                store.execute(
                    "DELETE FROM conversation_history WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
                    (agent_id, thread_id, user_id),
                )
                store.execute(
                    "DELETE FROM stream_events WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
                    (agent_id, thread_id, user_id),
                )
                store.commit()
                return True
            finally:
                store.close()
        except Exception as e:
            logger.error(f"Error deleting thread: {e}")
            return False

    def get_all_threads_for_agent(self, agent_id: str, user_id: str) -> List[Dict[str, Any]]:
        try:
            store = self._get_store()
            try:
                rows = store.fetchall(
                    """
                    SELECT thread_id, MAX(version) as latest_version, MAX(updated_at) as updated_at
                    FROM conversation_history
                    WHERE agent_id = ? AND user_id = ?
                    GROUP BY thread_id
                    ORDER BY updated_at DESC
                    """,
                    (agent_id, user_id),
                )
                threads = []
                for row in rows:
                    thread_id = row["thread_id"]
                    latest_version = row["latest_version"]
                    updated_at = row["updated_at"]
                    messages_row = store.fetchone(
                        """
                        SELECT messages FROM conversation_history
                        WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                        """,
                        (agent_id, thread_id, latest_version, user_id),
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
                        stream_row = store.fetchone(
                            """
                            SELECT events FROM stream_events
                            WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                            """,
                            (agent_id, thread_id, user_id),
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
                store.close()
        except Exception as e:
            logger.error(f"Error getting threads for agent: {e}")
            return []

    def save_stream_events(
        self, agent_id: str, thread_id: str, user_id: str, events: List[Dict[str, Any]]
    ) -> bool:
        try:
            store = self._get_store()
            try:
                now = datetime.utcnow().isoformat()
                events_json = json.dumps(events)
                existing = store.fetchone(
                    "SELECT created_at FROM stream_events WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
                    (agent_id, thread_id, user_id),
                )
                if existing:
                    row = store.fetchone(
                        "SELECT events FROM stream_events WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
                        (agent_id, thread_id, user_id),
                    )
                    existing_events = json.loads(row["events"]) if row and row["events"] else []
                    combined_events = existing_events + events
                    store.execute(
                        """
                        UPDATE stream_events SET events = ?, updated_at = ?
                        WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                        """,
                        (json.dumps(combined_events), now, agent_id, thread_id, user_id),
                    )
                else:
                    store.execute(
                        """
                        INSERT INTO stream_events (agent_id, thread_id, user_id, events, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (agent_id, thread_id, user_id, events_json, now, now),
                    )
                store.commit()
                return True
            finally:
                store.close()
        except Exception as e:
            logger.error(f"Error saving stream events: {e}")
            return False

    def get_stream_events(
        self, agent_id: str, thread_id: str, user_id: str
    ) -> Optional[ConversationStreamHistory]:
        try:
            store = self._get_store()
            try:
                row = store.fetchone(
                    """
                    SELECT agent_id, thread_id, user_id, events, created_at, updated_at
                    FROM stream_events WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                    """,
                    (agent_id, thread_id, user_id),
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
                store.close()
        except Exception as e:
            logger.error(f"Error retrieving stream events: {e}")
            return None

    def append_stream_event(
        self, agent_id: str, thread_id: str, user_id: str, event_name: str, event_data: str, sequence: int
    ) -> bool:
        """
        Append a single streaming event to the conversation.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            user_id: The user identifier
            event_name: Name of the event (e.g., 'CodeAgent', 'Thinking', 'Answer')
            event_data: The event data as string
            sequence: Sequence number of the event

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get existing events
            stream_history = self.get_stream_events(agent_id, thread_id, user_id)

            # Create new event as dictionary
            new_event = {
                "event_name": event_name,
                "event_data": event_data,
                "timestamp": datetime.utcnow().isoformat(),
                "sequence": sequence,
            }

            # Prepare events list
            events_list: List[Dict[str, Any]]
            if stream_history:
                # Convert existing Pydantic models to dicts and append new event
                events_list = [
                    event.model_dump() if hasattr(event, 'model_dump') else dict(event)
                    for event in stream_history.events
                ]
                events_list.append(new_event)
            else:
                # Create new events list
                events_list = [new_event]

            # Save updated events
            return self.save_stream_events(agent_id, thread_id, user_id, events_list)

        except Exception as e:
            logger.error(f"Error appending stream event: {e}")
            return False


# Global instance
_conversation_db: Optional[ConversationHistoryDB] = None


def get_conversation_db() -> ConversationHistoryDB:
    """Get or create the global conversation history database instance"""
    global _conversation_db
    if _conversation_db is None:
        _conversation_db = ConversationHistoryDB()
    return _conversation_db
