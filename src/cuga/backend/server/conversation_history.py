"""
Conversation History Persistence Module

This module provides functionality to persist conversation history to a database.
Each conversation is stored with multiple keys: agent_id, thread_id, version, and user_id.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from loguru import logger
from pydantic import BaseModel

from cuga.config import DBS_DIR


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

        Args:
            db_path: Path to the SQLite database file. If None, uses default location in DBS_DIR.
        """
        if db_path is None:
            # Store in DBS_DIR alongside other databases (manage_config.db, milvus_policies.db, etc.)
            db_path = str(Path(DBS_DIR) / "conversation_history.db")

        self.db_path = db_path
        self._ensure_db_exists()
        logger.info(f"Conversation history database initialized at: {self.db_path}")

    def _ensure_db_exists(self):
        """Create the database and tables if they don't exist"""
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create conversation_history table with composite key
        cursor.execute("""
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

        # Create stream_events table for storing streaming events
        cursor.execute("""
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

        # Create indexes for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_thread_id
            ON conversation_history(thread_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id
            ON conversation_history(user_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_id
            ON conversation_history(agent_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_updated_at
            ON conversation_history(updated_at)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_stream_thread_id
            ON stream_events(thread_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_stream_user_id
            ON stream_events(user_id)
        """)

        conn.commit()
        conn.close()
        logger.info("Conversation history database tables created/verified")

    def save_conversation(
        self, agent_id: str, thread_id: str, version: int, user_id: str, messages: List[Dict[str, Any]]
    ) -> bool:
        """
        Save or update a conversation in the database.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            version: The version number of the conversation
            user_id: The user identifier
            messages: List of message dictionaries

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = datetime.utcnow().isoformat()
            messages_json = json.dumps(messages)

            # Check if conversation exists
            cursor.execute(
                """
                SELECT created_at FROM conversation_history
                WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
            """,
                (agent_id, thread_id, version, user_id),
            )

            existing = cursor.fetchone()

            if existing:
                # Update existing conversation
                cursor.execute(
                    """
                    UPDATE conversation_history
                    SET messages = ?, updated_at = ?
                    WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                """,
                    (messages_json, now, agent_id, thread_id, version, user_id),
                )
                logger.debug(
                    f"Updated conversation: agent_id={agent_id}, thread_id={thread_id}, version={version}, user_id={user_id}"
                )
            else:
                # Insert new conversation
                cursor.execute(
                    """
                    INSERT INTO conversation_history 
                    (agent_id, thread_id, version, user_id, messages, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (agent_id, thread_id, version, user_id, messages_json, now, now),
                )
                logger.debug(
                    f"Created new conversation: agent_id={agent_id}, thread_id={thread_id}, version={version}, user_id={user_id}"
                )

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error saving conversation: {e}")
            return False

    def get_conversation(
        self, agent_id: str, thread_id: str, version: int, user_id: str
    ) -> Optional[ConversationHistory]:
        """
        Retrieve a conversation from the database.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            version: The version number of the conversation
            user_id: The user identifier

        Returns:
            ConversationHistory object if found, None otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                FROM conversation_history
                WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
            """,
                (agent_id, thread_id, version, user_id),
            )

            row = cursor.fetchone()
            conn.close()

            if row:
                return ConversationHistory(
                    agent_id=row[0],
                    thread_id=row[1],
                    version=row[2],
                    user_id=row[3],
                    messages=json.loads(row[4]),
                    created_at=row[5],
                    updated_at=row[6],
                )
            return None

        except Exception as e:
            logger.error(f"Error retrieving conversation: {e}")
            return None

    def get_thread_history(self, thread_id: str, user_id: Optional[str] = None) -> List[ConversationHistory]:
        """
        Get all conversation versions for a specific thread.

        Args:
            thread_id: The thread/conversation identifier
            user_id: Optional user filter

        Returns:
            List of ConversationHistory objects
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            if user_id:
                cursor.execute(
                    """
                    SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                    FROM conversation_history
                    WHERE thread_id = ? AND user_id = ?
                    ORDER BY version DESC
                """,
                    (thread_id, user_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT agent_id, thread_id, version, user_id, messages, created_at, updated_at
                    FROM conversation_history
                    WHERE thread_id = ?
                    ORDER BY version DESC
                """,
                    (thread_id,),
                )

            rows = cursor.fetchall()
            conn.close()

            return [
                ConversationHistory(
                    agent_id=row[0],
                    thread_id=row[1],
                    version=row[2],
                    user_id=row[3],
                    messages=json.loads(row[4]),
                    created_at=row[5],
                    updated_at=row[6],
                )
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Error retrieving thread history: {e}")
            return []

    def get_latest_version(self, agent_id: str, thread_id: str, user_id: str) -> int:
        """
        Get the latest version number for a conversation.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            user_id: The user identifier

        Returns:
            Latest version number, or 0 if no versions exist
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT MAX(version) FROM conversation_history
                WHERE agent_id = ? AND thread_id = ? AND user_id = ?
            """,
                (agent_id, thread_id, user_id),
            )

            result = cursor.fetchone()
            conn.close()

            return result[0] if result[0] is not None else 0

        except Exception as e:
            logger.error(f"Error getting latest version: {e}")
            return 0

    def delete_conversation(self, agent_id: str, thread_id: str, version: int, user_id: str) -> bool:
        """
        Delete a specific conversation version.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            version: The version number of the conversation
            user_id: The user identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                DELETE FROM conversation_history
                WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
            """,
                (agent_id, thread_id, version, user_id),
            )

            conn.commit()
            conn.close()
            logger.info(
                f"Deleted conversation: agent_id={agent_id}, thread_id={thread_id}, version={version}, user_id={user_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error deleting conversation: {e}")
            return False

    def delete_stream_events(self, agent_id: str, thread_id: str, user_id: str) -> bool:
        """
        Delete stream events for a specific thread.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            user_id: The user identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                DELETE FROM stream_events
                WHERE agent_id = ? AND thread_id = ? AND user_id = ?
            """,
                (agent_id, thread_id, user_id),
            )

            conn.commit()
            conn.close()
            logger.info(
                f"Deleted stream events: agent_id={agent_id}, thread_id={thread_id}, user_id={user_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error deleting stream events: {e}")
            return False

    def delete_thread(self, agent_id: str, thread_id: str, user_id: str) -> bool:
        """
        Delete all versions of a thread and its stream events.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            user_id: The user identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Delete conversation history
            cursor.execute(
                """
                DELETE FROM conversation_history
                WHERE agent_id = ? AND thread_id = ? AND user_id = ?
            """,
                (agent_id, thread_id, user_id),
            )

            # Delete stream events
            cursor.execute(
                """
                DELETE FROM stream_events
                WHERE agent_id = ? AND thread_id = ? AND user_id = ?
            """,
                (agent_id, thread_id, user_id),
            )

            conn.commit()
            conn.close()
            logger.info(
                f"Deleted thread and stream events: agent_id={agent_id}, thread_id={thread_id}, user_id={user_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error deleting thread: {e}")
            return False

    def get_all_threads_for_agent(self, agent_id: str, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all unique threads for an agent with their latest version and first user message.

        Args:
            agent_id: The agent identifier
            user_id: The user identifier

        Returns:
            List of dictionaries containing thread_id, latest_version, first_message, and updated_at
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get all unique threads with their latest version
            cursor.execute(
                """
                SELECT
                    thread_id,
                    MAX(version) as latest_version,
                    MAX(updated_at) as updated_at
                FROM conversation_history
                WHERE agent_id = ? AND user_id = ?
                GROUP BY thread_id
                ORDER BY updated_at DESC
            """,
                (agent_id, user_id),
            )

            threads = []
            for row in cursor.fetchall():
                thread_id = row[0]
                latest_version = row[1]
                updated_at = row[2]

                # Get the conversation for this thread's latest version
                cursor.execute(
                    """
                    SELECT messages FROM conversation_history
                    WHERE agent_id = ? AND thread_id = ? AND version = ? AND user_id = ?
                """,
                    (agent_id, thread_id, latest_version, user_id),
                )

                messages_row = cursor.fetchone()
                first_message = "New Conversation"

                if messages_row:
                    messages = json.loads(messages_row[0])
                    # Find first user message in conversation history
                    for msg in messages:
                        role = msg.get("role", "").lower()
                        if role == "user" or role == "human":
                            content = msg.get("content", "")
                            if content and content.strip():
                                # Truncate to first 60 characters
                                first_message = content[:60] + "..." if len(content) > 60 else content
                                break

                # If no user message found in conversation history, check stream events
                if first_message == "New Conversation":
                    cursor.execute(
                        """
                        SELECT events FROM stream_events
                        WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                    """,
                        (agent_id, thread_id, user_id),
                    )

                    stream_row = cursor.fetchone()
                    if stream_row:
                        events = json.loads(stream_row[0])
                        # Find first UserMessage event
                        for event in events:
                            if event.get("event_name") == "UserMessage":
                                event_data = event.get("event_data", "")
                                if event_data and event_data.strip():
                                    # Truncate to first 60 characters
                                    first_message = (
                                        event_data[:60] + "..." if len(event_data) > 60 else event_data
                                    )
                                    break

                # Add thread to list (moved outside the if block)
                threads.append(
                    {
                        "thread_id": thread_id,
                        "latest_version": latest_version,
                        "first_message": first_message,
                        "updated_at": updated_at,
                    }
                )

            conn.close()
            return threads

        except Exception as e:
            logger.error(f"Error getting threads for agent: {e}")
            return []

    def save_stream_events(
        self, agent_id: str, thread_id: str, user_id: str, events: List[Dict[str, Any]]
    ) -> bool:
        """
        Save or update streaming events for a conversation.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            user_id: The user identifier
            events: List of event dictionaries with 'event_name', 'event_data', 'timestamp', 'sequence'

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = datetime.utcnow().isoformat()
            events_json = json.dumps(events)

            # Check if stream events exist
            cursor.execute(
                """
                SELECT created_at FROM stream_events
                WHERE agent_id = ? AND thread_id = ? AND user_id = ?
            """,
                (agent_id, thread_id, user_id),
            )

            existing = cursor.fetchone()

            if existing:
                # Get existing events and append new ones
                cursor.execute(
                    """
                    SELECT events FROM stream_events
                    WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                """,
                    (agent_id, thread_id, user_id),
                )

                existing_events_json = cursor.fetchone()[0]
                existing_events = json.loads(existing_events_json) if existing_events_json else []

                # Append new events to existing ones
                combined_events = existing_events + events
                combined_events_json = json.dumps(combined_events)

                # Update with combined events
                cursor.execute(
                    """
                    UPDATE stream_events
                    SET events = ?, updated_at = ?
                    WHERE agent_id = ? AND thread_id = ? AND user_id = ?
                """,
                    (combined_events_json, now, agent_id, thread_id, user_id),
                )
                logger.debug(
                    f"Appended {len(events)} new events to existing {len(existing_events)} events: agent_id={agent_id}, thread_id={thread_id}, user_id={user_id}"
                )
            else:
                # Insert new stream events
                cursor.execute(
                    """
                    INSERT INTO stream_events 
                    (agent_id, thread_id, user_id, events, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (agent_id, thread_id, user_id, events_json, now, now),
                )
                logger.debug(
                    f"Created new stream events: agent_id={agent_id}, thread_id={thread_id}, user_id={user_id}"
                )

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.error(f"Error saving stream events: {e}")
            return False

    def get_stream_events(
        self, agent_id: str, thread_id: str, user_id: str
    ) -> Optional[ConversationStreamHistory]:
        """
        Retrieve streaming events for a conversation.

        Args:
            agent_id: The agent identifier
            thread_id: The thread/conversation identifier
            user_id: The user identifier

        Returns:
            ConversationStreamHistory object if found, None otherwise
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT agent_id, thread_id, user_id, events, created_at, updated_at
                FROM stream_events
                WHERE agent_id = ? AND thread_id = ? AND user_id = ?
            """,
                (agent_id, thread_id, user_id),
            )

            row = cursor.fetchone()
            conn.close()

            if row:
                return ConversationStreamHistory(
                    agent_id=row[0],
                    thread_id=row[1],
                    user_id=row[2],
                    events=json.loads(row[3]),
                    created_at=row[4],
                    updated_at=row[5],
                )
            return None

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
