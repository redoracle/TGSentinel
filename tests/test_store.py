"""Unit tests for store module."""

import pytest
from sqlalchemy import text

from tgsentinel.store import init_db, mark_alerted, upsert_message


@pytest.mark.unit
class TestInitDb:
    """Test database initialization."""

    def test_init_db_creates_tables(self):
        """Test that init_db creates required tables."""
        engine = init_db("sqlite:///:memory:")

        with engine.connect() as conn:
            # Check messages table exists
            result = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
                )
            )
            assert result.fetchone() is not None

            # Check feedback table exists
            result = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback'"
                )
            )
            assert result.fetchone() is not None

    def test_init_db_messages_schema(self):
        """Test messages table has correct schema."""
        engine = init_db("sqlite:///:memory:")

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(messages)"))
            columns = {row[1]: row[2] for row in result.fetchall()}

            assert "chat_id" in columns
            assert "msg_id" in columns
            assert "content_hash" in columns
            assert "score" in columns
            assert "alerted" in columns
            assert "created_at" in columns

    def test_init_db_feedback_schema(self):
        """Test feedback table has correct schema."""
        engine = init_db("sqlite:///:memory:")

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(feedback)"))
            columns = {row[1]: row[2] for row in result.fetchall()}

            assert "chat_id" in columns
            assert "msg_id" in columns
            assert "label" in columns
            assert "created_at" in columns


class TestUpsertMessage:
    """Test message upsert functionality."""

    def test_upsert_message_insert(self, in_memory_db):
        """Test inserting a new message."""
        chat_id = -100123456789
        msg_id = 12345
        content_hash_val = "abc123"
        score = 1.5

        upsert_message(in_memory_db, chat_id, msg_id, content_hash_val, score)

        with in_memory_db.connect() as conn:
            result = conn.execute(
                text("SELECT * FROM messages WHERE chat_id=:c AND msg_id=:m"),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()

            assert row is not None
            assert row[0] == chat_id
            assert row[1] == msg_id
            assert row[2] == content_hash_val
            assert row[3] == score
            assert row[4] == 0  # alerted defaults to 0

    def test_upsert_message_update(self, in_memory_db):
        """Test updating an existing message."""
        chat_id = -100123456789
        msg_id = 12345

        # Insert initial message
        upsert_message(in_memory_db, chat_id, msg_id, "hash1", 1.0)

        # Update with new hash and score
        upsert_message(in_memory_db, chat_id, msg_id, "hash2", 2.5)

        with in_memory_db.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT content_hash, score FROM messages WHERE chat_id=:c AND msg_id=:m"
                ),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()

            assert row is not None
            assert row[0] == "hash2"
            assert row[1] == 2.5

    def test_upsert_message_multiple(self, in_memory_db):
        """Test inserting multiple messages."""
        messages = [
            (-100123, 1, "hash1", 1.0),
            (-100123, 2, "hash2", 2.0),
            (-100456, 1, "hash3", 3.0),
        ]

        for chat_id, msg_id, h, score in messages:
            upsert_message(in_memory_db, chat_id, msg_id, h, score)

        with in_memory_db.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM messages"))
            count = result.fetchone()[0]
            assert count == 3


class TestMarkAlerted:
    """Test message alert marking."""

    def test_mark_alerted(self, in_memory_db):
        """Test marking a message as alerted."""
        chat_id = -100123456789
        msg_id = 12345

        # Insert a message
        upsert_message(in_memory_db, chat_id, msg_id, "hash1", 1.5)

        # Mark as alerted
        mark_alerted(in_memory_db, chat_id, msg_id)

        with in_memory_db.connect() as conn:
            result = conn.execute(
                text("SELECT alerted FROM messages WHERE chat_id=:c AND msg_id=:m"),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()

            assert row is not None
            assert row[0] == 1

    def test_mark_alerted_nonexistent(self, in_memory_db):
        """Test marking non-existent message doesn't raise error."""
        # Should not raise an error even if message doesn't exist
        mark_alerted(in_memory_db, -999, 999)

    def test_mark_alerted_multiple_times(self, in_memory_db):
        """Test marking a message alerted multiple times is idempotent."""
        chat_id = -100123456789
        msg_id = 12345

        # Insert a message
        upsert_message(in_memory_db, chat_id, msg_id, "hash1", 1.5)

        # Mark as alerted multiple times
        mark_alerted(in_memory_db, chat_id, msg_id)
        mark_alerted(in_memory_db, chat_id, msg_id)
        mark_alerted(in_memory_db, chat_id, msg_id)

        with in_memory_db.connect() as conn:
            result = conn.execute(
                text("SELECT alerted FROM messages WHERE chat_id=:c AND msg_id=:m"),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()

            assert row is not None
            assert row[0] == 1


class TestIntegration:
    """Integration tests for store module."""

    def test_full_workflow(self, in_memory_db):
        """Test full workflow: insert, update, mark alerted."""
        chat_id = -100123456789
        msg_id = 12345

        # Insert message
        upsert_message(in_memory_db, chat_id, msg_id, "hash1", 1.0)

        # Verify initial state
        with in_memory_db.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT score, alerted FROM messages WHERE chat_id=:c AND msg_id=:m"
                ),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()
            assert row[0] == 1.0
            assert row[1] == 0

        # Update score
        upsert_message(in_memory_db, chat_id, msg_id, "hash2", 2.5)

        # Verify update
        with in_memory_db.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT score, alerted FROM messages WHERE chat_id=:c AND msg_id=:m"
                ),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()
            assert row[0] == 2.5
            assert row[1] == 0

        # Mark alerted
        mark_alerted(in_memory_db, chat_id, msg_id)

        # Verify alerted
        with in_memory_db.connect() as conn:
            result = conn.execute(
                text("SELECT alerted FROM messages WHERE chat_id=:c AND msg_id=:m"),
                {"c": chat_id, "m": msg_id},
            )
            row = result.fetchone()
            assert row[0] == 1
