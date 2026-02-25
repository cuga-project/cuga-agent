"""
Test script for conversation history persistence
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.cuga_graph.state.agent_state import default_state
from langchain_core.messages import HumanMessage, AIMessage


def test_conversation_history_db():
    """Test the conversation history database functionality"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        from cuga.backend.storage import facade as storage_facade

        original_local_db_path = storage_facade._local_db_path
        storage_facade._local_db_path = lambda: db_path

        print("=" * 80)
        print("Testing Conversation History Database")
        print("=" * 80)

        db = ConversationHistoryDB()
        print(f"✓ Database initialized at: {db_path}")

        # Test data
        agent_id = "test-agent"
        thread_id = "test-thread-123"
        user_id = "test-user"
        version = 1

        # Create test messages
        messages = [
            {
                "role": "user",
                "content": "Hello, how are you?",
                "timestamp": "2024-01-01T00:00:00",
                "metadata": {"type": "HumanMessage"},
            },
            {
                "role": "assistant",
                "content": "I'm doing well, thank you!",
                "timestamp": "2024-01-01T00:00:01",
                "metadata": {"type": "AIMessage"},
            },
        ]

        # Test 1: Save conversation
        print("\n1. Testing save_conversation...")
        success = db.save_conversation(
            agent_id=agent_id, thread_id=thread_id, version=version, user_id=user_id, messages=messages
        )
        assert success, "Failed to save conversation"
        print(f"✓ Saved conversation: agent_id={agent_id}, thread_id={thread_id}, version={version}")

        # Test 2: Retrieve conversation
        print("\n2. Testing get_conversation...")
        retrieved = db.get_conversation(
            agent_id=agent_id, thread_id=thread_id, version=version, user_id=user_id
        )
        assert retrieved is not None, "Failed to retrieve conversation"
        assert retrieved.agent_id == agent_id
        assert retrieved.thread_id == thread_id
        assert retrieved.version == version
        assert retrieved.user_id == user_id
        assert len(retrieved.messages) == 2
        print(f"✓ Retrieved conversation with {len(retrieved.messages)} messages")

        # Test 3: Update conversation (add more messages)
        print("\n3. Testing update_conversation...")
        messages.append(
            {
                "role": "user",
                "content": "What can you help me with?",
                "timestamp": "2024-01-01T00:00:02",
                "metadata": {"type": "HumanMessage"},
            }
        )
        success = db.save_conversation(
            agent_id=agent_id, thread_id=thread_id, version=version, user_id=user_id, messages=messages
        )
        assert success, "Failed to update conversation"
        retrieved = db.get_conversation(agent_id, thread_id, version, user_id)
        assert len(retrieved.messages) == 3
        print(f"✓ Updated conversation, now has {len(retrieved.messages)} messages")

        # Test 4: Get latest version
        print("\n4. Testing get_latest_version...")
        latest = db.get_latest_version(agent_id, thread_id, user_id)
        assert latest == version
        print(f"✓ Latest version: {latest}")

        # Test 5: Save new version
        print("\n5. Testing save new version...")
        version_2 = 2
        messages_v2 = [
            {
                "role": "user",
                "content": "New conversation",
                "timestamp": "2024-01-01T01:00:00",
                "metadata": {"type": "HumanMessage"},
            }
        ]
        success = db.save_conversation(
            agent_id=agent_id, thread_id=thread_id, version=version_2, user_id=user_id, messages=messages_v2
        )
        assert success
        latest = db.get_latest_version(agent_id, thread_id, user_id)
        assert latest == version_2
        print(f"✓ Saved version {version_2}, latest version is now: {latest}")

        # Test 6: Get thread history
        print("\n6. Testing get_thread_history...")
        history = db.get_thread_history(thread_id, user_id)
        assert len(history) == 2
        print(f"✓ Retrieved thread history with {len(history)} versions")

        # Test 7: Delete specific version
        print("\n7. Testing delete_conversation...")
        success = db.delete_conversation(agent_id, thread_id, version, user_id)
        assert success
        retrieved = db.get_conversation(agent_id, thread_id, version, user_id)
        assert retrieved is None
        print(f"✓ Deleted version {version}")

        # Test 8: Delete entire thread
        print("\n8. Testing delete_thread...")
        success = db.delete_thread(agent_id, thread_id, user_id)
        assert success
        history = db.get_thread_history(thread_id, user_id)
        assert len(history) == 0
        print("✓ Deleted entire thread")

        print("\n" + "=" * 80)
        print("All tests passed! ✓")
        print("=" * 80)

    finally:
        storage_facade._local_db_path = original_local_db_path
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"\nCleaned up test database: {db_path}")


def test_with_agent_state():
    """Test saving conversation from AgentState"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        from cuga.backend.storage import facade as storage_facade

        original_local_db_path = storage_facade._local_db_path
        storage_facade._local_db_path = lambda: db_path

        print("\n" + "=" * 80)
        print("Testing with AgentState")
        print("=" * 80)

        db = ConversationHistoryDB()

        # Create a mock AgentState with messages
        state = default_state(page=None, observation=None, goal="Test goal")
        state.thread_id = "test-thread-456"
        state.user_id = "test-user"

        # Add some messages
        state.chat_messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there!"),
            HumanMessage(content="How are you?"),
            AIMessage(content="I'm doing great!"),
        ]

        # Convert messages to serializable format (similar to save_conversation_to_db)
        messages = []
        for msg in state.chat_messages:
            messages.append(
                {
                    "role": "user" if isinstance(msg, HumanMessage) else "assistant",
                    "content": msg.content,
                    "timestamp": "2024-01-01T00:00:00",
                    "metadata": {"type": type(msg).__name__},
                }
            )

        # Save
        success = db.save_conversation(
            agent_id="test-agent",
            thread_id=state.thread_id,
            version=1,
            user_id=state.user_id,
            messages=messages,
        )

        assert success
        print(f"✓ Saved conversation from AgentState with {len(messages)} messages")

        # Retrieve and verify
        retrieved = db.get_conversation("test-agent", state.thread_id, 1, state.user_id)
        assert retrieved is not None
        assert len(retrieved.messages) == 4
        print(f"✓ Retrieved conversation, verified {len(retrieved.messages)} messages")

        print("\n" + "=" * 80)
        print("AgentState test passed! ✓")
        print("=" * 80)

    finally:
        storage_facade._local_db_path = original_local_db_path
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    try:
        test_conversation_history_db()
        test_with_agent_state()
        print("\n🎉 All tests completed successfully!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
