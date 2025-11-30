"""Unit tests for digest module."""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from tgsentinel.digest import build_digest_query, send_digest


@pytest.mark.unit
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
            channels_config=None,
        )

        # Should not send any messages
        client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_digest_with_messages_dm_mode(self, in_memory_db):
        """Test sending digest in DM mode."""
        from tgsentinel.config import ChannelRule
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert some test messages
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_for_interest_feed(in_memory_db, -100123, 1)
        upsert_message(in_memory_db, -100123, 2, "hash2", 1.5)
        mark_for_interest_feed(in_memory_db, -100123, 2)

        # Mock Telegram client with message fetching
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Test message content"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "TestUser"
        mock_sender.last_name = None
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        # Create channel config
        channels = [ChannelRule(-100123, name="Test Channel")]

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
            channels_config=channels,
        )

        # Should send one message to 'me'
        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == "me"
        assert "🗞️" in call_args[0][1]
        assert "Digest" in call_args[0][1]
        assert "Test Channel" in call_args[0][1]  # Channel name should be in digest

    @pytest.mark.asyncio
    async def test_send_digest_with_messages_channel_mode(self, in_memory_db):
        """Test sending digest in digest mode (formerly channel mode)."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert a test message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_for_interest_feed(in_memory_db, -100123, 1)

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Test message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="digest",
            channel="@your_notification_bot",
            channels_config=None,
        )

        # Should send one message to the channel
        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == "@your_notification_bot"
        assert "🗞️" in call_args[0][1]
        assert "Digest" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_send_digest_with_messages_both_mode(self, in_memory_db):
        """Test sending digest in both mode."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert a test message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_for_interest_feed(in_memory_db, -100123, 1)

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Test message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="both",
            channel="@your_notification_bot",
            channels_config=None,
        )

        # Should send two messages: one to 'me' and one to channel
        assert client.send_message.call_count == 2
        calls = client.send_message.call_args_list
        targets = [call[0][0] for call in calls]
        assert "me" in targets
        assert "@your_notification_bot" in targets

    @pytest.mark.asyncio
    async def test_send_digest_respects_top_n(self, in_memory_db):
        """Test that digest respects top_n limit."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert many messages
        for i in range(20):
            upsert_message(in_memory_db, -100123, i, f"hash{i}", float(i))
            mark_for_interest_feed(in_memory_db, -100123, i)

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Test message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=5,
            mode="dm",
            channel="",
            channels_config=None,
        )

        client.send_message.assert_called_once()
        message_text = client.send_message.call_args[0][1]

        # Should mention top 5 (new format says "Top 5 messages")
        assert "Top 5" in message_text

    @pytest.mark.asyncio
    async def test_send_digest_orders_by_score_desc(self, in_memory_db):
        """Test that digest orders messages by score descending."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert messages with different scores
        upsert_message(in_memory_db, -100123, 1, "hash1", 1.0)
        mark_for_interest_feed(in_memory_db, -100123, 1)
        upsert_message(in_memory_db, -100123, 2, "hash2", 3.0)
        mark_for_interest_feed(in_memory_db, -100123, 2)
        upsert_message(in_memory_db, -100123, 3, "hash3", 2.0)
        mark_for_interest_feed(in_memory_db, -100123, 3)

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Test message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
            channels_config=None,
        )

        message_text = client.send_message.call_args[0][1]

        # Check that message 2 (score 3.0) appears before message 1 (score 1.0)
        idx_msg2 = message_text.find("3.00")
        idx_msg1 = message_text.find("1.00")
        assert idx_msg2 < idx_msg1, "Messages should be ordered by score descending"

    @pytest.mark.asyncio
    async def test_send_digest_filters_by_time_window(self, in_memory_db):
        """Test that digest filters messages by time window."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert a recent message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_for_interest_feed(in_memory_db, -100123, 1)

        # Insert an old message (manually set created_at to old date)
        with in_memory_db.begin() as con:
            old_date = dt.datetime.now(dt.UTC) - dt.timedelta(hours=48)
            old_date_str = old_date.strftime("%Y-%m-%d %H:%M:%S")
            con.execute(
                text(
                    """
                    INSERT INTO messages(chat_id, msg_id, content_hash, score, flagged_for_interest_feed, created_at)
                    VALUES(:c, :m, :h, :s, 1, :t)
                """
                ),
                {"c": -100123, "m": 999, "h": "old_hash", "s": 5.0, "t": old_date_str},
            )

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Recent message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        # Query for last 24 hours only
        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
            channels_config=None,
        )

        message_text = client.send_message.call_args[0][1]

        # Should only include recent message (2.5), not old one (5.0)
        assert "2.50" in message_text
        assert "5.00" not in message_text

    @pytest.mark.asyncio
    async def test_send_digest_only_includes_alerted_messages(self, in_memory_db):
        """Test that digest only includes alerted messages."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        # Insert alerted message
        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_for_interest_feed(in_memory_db, -100123, 1)

        # Insert non-alerted message
        upsert_message(in_memory_db, -100123, 2, "hash2", 3.5)
        # Don't mark as alerted

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
            channels_config=None,
        )

        message_text = client.send_message.call_args[0][1]

        # Should include alerted message's score (2.5) but not non-alerted (3.5)
        assert "2.50" in message_text
        assert "3.50" not in message_text

    @pytest.mark.asyncio
    async def test_send_digest_formats_score_correctly(self, in_memory_db):
        """Test that digest formats scores with 2 decimal places."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        upsert_message(in_memory_db, -100123, 1, "hash1", 2.567)
        mark_for_interest_feed(in_memory_db, -100123, 1)

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db,
            client,
            since_hours=24,
            top_n=10,
            mode="dm",
            channel="",
            channels_config=None,
        )

        message_text = client.send_message.call_args[0][1]

        # Score should be formatted to 2 decimal places
        assert "2.57" in message_text

    @pytest.mark.asyncio
    async def test_send_digest_channel_mode_without_channel(self, in_memory_db):
        """Test that digest mode without channel specified doesn't send to channel."""
        from tgsentinel.store import mark_for_interest_feed, upsert_message

        upsert_message(in_memory_db, -100123, 1, "hash1", 2.5)
        mark_for_interest_feed(in_memory_db, -100123, 1)

        # Mock Telegram client
        client = AsyncMock()
        mock_message = MagicMock()
        mock_message.text = "Test message"
        mock_message.date = dt.datetime.now()
        mock_sender = MagicMock()
        mock_sender.first_name = "User"
        mock_message.sender = mock_sender
        client.get_messages = AsyncMock(return_value=mock_message)

        await send_digest(
            in_memory_db, client, since_hours=24, top_n=10, mode="digest", channel=""
        )

        # Should send to saved messages (default when no channel specified)
        client.send_message.assert_called_once()


@pytest.mark.unit
class TestDigestQuery:
    """Test the build_digest_query function."""

    def test_digest_query_alerts_syntax(self):
        """Test that build_digest_query generates valid SQL for alerts."""
        query = build_digest_query("alerts")
        assert isinstance(query, str)
        assert len(query) > 0
        assert "SELECT" in query
        assert "FROM messages" in query
        assert "WHERE flagged_for_alerts_feed = 1" in query
        assert "ORDER BY score DESC" in query
        assert "LIMIT" in query

    def test_digest_query_interests_syntax(self):
        """Test that build_digest_query generates valid SQL for interests."""
        query = build_digest_query("interests")
        assert isinstance(query, str)
        assert len(query) > 0
        assert "SELECT" in query
        assert "FROM messages" in query
        assert "WHERE flagged_for_interest_feed = 1" in query
        assert "ORDER BY score DESC" in query
        assert "LIMIT" in query
