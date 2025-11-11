"""Unit tests for digest module."""

import pytest
import datetime as dt
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import text
from tgsentinel.digest import send_digest, DIGEST_QUERY


class TestSendDigest:
    """Test digest sending functionality."""

    @pytest.mark.asyncio
    async def test_send_digest_no_messages(self, in_memory_db):
        """Test sending digest when there are no messages."""
        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=1,
            top_n=10,
            mode="dm",
            channel="",
        )

        # Should not send any messages
        client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_digest_with_messages_dm_mode(self, in_memory_db):
        """Test sending digest in DM mode."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert some test messages
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_alerted(in_memory_db, -100123, 1)
        upsert_message(in_memory_db, -100123, 2, "hash2", 1.5)
        mark_alerted(in_memory_db, -100123, 2)

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
        )

        # Should send one message to 'me'
        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == "me"
        assert "🗞️ Digest" in call_args[0][1]
        assert "Top 10 highlights" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_send_digest_with_messages_channel_mode(self, in_memory_db):
        """Test sending digest in channel mode."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert a test message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_alerted(in_memory_db, -100123, 1)

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="channel",
            channel="@kit_red_bot",
        )

        # Should send one message to the channel
        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == "@kit_red_bot"
        assert "🗞️ Digest" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_send_digest_with_messages_both_mode(self, in_memory_db):
        """Test sending digest in both mode."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert a test message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_alerted(in_memory_db, -100123, 1)

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="both",
            channel="@kit_red_bot",
        )

        # Should send two messages: one to 'me' and one to channel
        assert client.send_message.call_count == 2
        calls = client.send_message.call_args_list
        targets = [call[0][0] for call in calls]
        assert "me" in targets
        assert "@kit_red_bot" in targets

    @pytest.mark.asyncio
    async def test_send_digest_respects_top_n(self, in_memory_db):
        """Test that digest respects top_n limit."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert many messages
        for i in range(20):
            upsert_message(in_memory_db, -100123, i, f"hash{i}", float(i))
            mark_alerted(in_memory_db, -100123, i)

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=5,
            mode="dm",
            channel="",
        )

        client.send_message.assert_called_once()
        message_text = client.send_message.call_args[0][1]

        # Should mention top 5
        assert "Top 5 highlights" in message_text
        # Count number of message lines (each starts with "- chat")
        message_lines = [
            line for line in message_text.split("\n") if line.startswith("- chat")
        ]
        assert len(message_lines) == 5

    @pytest.mark.asyncio
    async def test_send_digest_orders_by_score_desc(self, in_memory_db):
        """Test that digest orders messages by score descending."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert messages with different scores
        upsert_message(in_memory_db, -100123, 1, "hash1", 1.0)
        mark_alerted(in_memory_db, -100123, 1)
        upsert_message(in_memory_db, -100123, 2, "hash2", 3.0)
        mark_alerted(in_memory_db, -100123, 2)
        upsert_message(in_memory_db, -100123, 3, "hash3", 2.0)
        mark_alerted(in_memory_db, -100123, 3)

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
        )

        message_text = client.send_message.call_args[0][1]

        # Check order: msg 2 (3.0), msg 3 (2.0), msg 1 (1.0)
        lines = message_text.split("\n")
        # First should be msg 2 with score 3.0
        assert "msg 2" in lines[1]
        assert "3.00" in lines[1]

    @pytest.mark.asyncio
    async def test_send_digest_filters_by_time_window(self, in_memory_db):
        """Test that digest filters messages by time window."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert a recent message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_alerted(in_memory_db, -100123, 1)

        # Insert an old message (manually set created_at to old date)
        with in_memory_db.begin() as con:
            old_date = dt.datetime.now(dt.UTC) - dt.timedelta(hours=48)
            old_date_str = old_date.strftime("%Y-%m-%d %H:%M:%S")
            con.execute(
                text(
                    """
                    INSERT INTO messages(chat_id, msg_id, content_hash, score, alerted, created_at)
                    VALUES(:c, :m, :h, :s, 1, :t)
                """
                ),
                {"c": -100123, "m": 999, "h": "old_hash", "s": 5.0, "t": old_date_str},
            )

        client = AsyncMock()

        # Query for last 24 hours only
        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
        )

        message_text = client.send_message.call_args[0][1]

        # Should only include recent message, not old one
        assert "msg 1" in message_text
        assert "msg 999" not in message_text

    @pytest.mark.asyncio
    async def test_send_digest_only_includes_alerted_messages(self, in_memory_db):
        """Test that digest only includes alerted messages."""
        from tgsentinel.store import upsert_message, mark_alerted

        # Insert alerted message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_alerted(in_memory_db, -100123, 1)

        # Insert non-alerted message
        upsert_message(in_memory_db, -100123, 2, "hash2", 3.5)
        # Don't mark as alerted

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
        )

        message_text = client.send_message.call_args[0][1]

        # Should only include alerted message
        assert "msg 1" in message_text
        assert "msg 2" not in message_text

    @pytest.mark.asyncio
    async def test_send_digest_formats_score_correctly(self, in_memory_db):
        """Test that digest formats scores with 2 decimal places."""
        from tgsentinel.store import upsert_message, mark_alerted

        upsert_message(in_memory_db, -100123, 1, "hash1", 2.567)
        mark_alerted(in_memory_db, -100123, 1)

        client = AsyncMock()

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
        )

        message_text = client.send_message.call_args[0][1]

        # Score should be formatted to 2 decimal places
        assert "2.57" in message_text

    @pytest.mark.asyncio
    async def test_send_digest_channel_mode_without_channel(self, in_memory_db):
        """Test that channel mode without channel specified doesn't crash."""
        from tgsentinel.store import upsert_message, mark_alerted

        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_alerted(in_memory_db, -100123, 1)

        client = AsyncMock()

        # Channel mode but no channel specified
        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="channel",
            channel="",
        )

        # Should not send to channel (empty channel)
        client.send_message.assert_not_called()


class TestDigestQuery:
    """Test the DIGEST_QUERY SQL query."""

    def test_digest_query_syntax(self):
        """Test that DIGEST_QUERY is valid SQL."""
        # Just check that it's a non-empty string with expected keywords
        assert isinstance(DIGEST_QUERY, str)
        assert len(DIGEST_QUERY) > 0
        assert "SELECT" in DIGEST_QUERY
        assert "FROM messages" in DIGEST_QUERY
        assert "WHERE alerted=1" in DIGEST_QUERY
        assert "ORDER BY score DESC" in DIGEST_QUERY
        assert "LIMIT" in DIGEST_QUERY
