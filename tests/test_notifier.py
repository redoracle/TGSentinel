"""Unit tests for notifier module."""

import pytest
from unittest.mock import AsyncMock
from tgsentinel.notifier import notify_dm, notify_channel


class TestNotifyDm:
    """Test DM notification functionality."""

    @pytest.mark.asyncio
    async def test_notify_dm_sends_message(self):
        """Test that notify_dm sends a message to 'me'."""
        client = AsyncMock()
        title = "Test Channel"
        text = "This is a test message"

        await notify_dm(client, title, text)

        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == "me"
        assert "🔔" in call_args[0][1]
        assert title in call_args[0][1]
        assert text in call_args[0][1]

    @pytest.mark.asyncio
    async def test_notify_dm_formats_message_correctly(self):
        """Test that notify_dm formats the message with emoji and title."""
        client = AsyncMock()
        title = "Important Channel"
        text = "Critical alert"

        await notify_dm(client, title, text)

        expected_message = f"🔔 {title}\n{text}"
        client.send_message.assert_called_once_with("me", expected_message)

    @pytest.mark.asyncio
    async def test_notify_dm_handles_empty_text(self):
        """Test that notify_dm handles empty text."""
        client = AsyncMock()
        title = "Test"
        text = ""

        await notify_dm(client, title, text)

        client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_dm_handles_long_text(self):
        """Test that notify_dm handles long text."""
        client = AsyncMock()
        title = "Test"
        text = "A" * 5000  # Very long text

        await notify_dm(client, title, text)

        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert len(call_args[0][1]) > 0


class TestNotifyChannel:
    """Test channel notification functionality."""

    @pytest.mark.asyncio
    async def test_notify_channel_sends_message(self):
        """Test that notify_channel sends a message to the specified channel."""
        client = AsyncMock()
        channel = "@mychannel"
        title = "Test Channel"
        text = "This is a test message"

        await notify_channel(client, channel, title, text)

        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == "@mychannel"
        assert "🔔" in call_args[0][1]
        assert title in call_args[0][1]
        assert text in call_args[0][1]

    @pytest.mark.asyncio
    async def test_notify_channel_formats_message_correctly(self):
        """Test that notify_channel formats the message correctly."""
        client = AsyncMock()
        channel = "-1001234567890"
        title = "Important Channel"
        text = "Critical alert"

        await notify_channel(client, channel, title, text)

        expected_message = f"🔔 {title}\n{text}"
        client.send_message.assert_called_once_with(channel, expected_message)

    @pytest.mark.asyncio
    async def test_notify_channel_with_username(self):
        """Test notifying a channel by username."""
        client = AsyncMock()
        channel = "@alerts_channel"
        title = "Test"
        text = "Message"

        await notify_channel(client, channel, title, text)

        client.send_message.assert_called_once()
        assert client.send_message.call_args[0][0] == "@alerts_channel"

    @pytest.mark.asyncio
    async def test_notify_channel_with_id(self):
        """Test notifying a channel by ID."""
        client = AsyncMock()
        channel = "-1001234567890"
        title = "Test"
        text = "Message"

        await notify_channel(client, channel, title, text)

        client.send_message.assert_called_once()
        assert client.send_message.call_args[0][0] == "-1001234567890"

    @pytest.mark.asyncio
    async def test_notify_channel_handles_empty_text(self):
        """Test that notify_channel handles empty text."""
        client = AsyncMock()
        channel = "@test"
        title = "Test"
        text = ""

        await notify_channel(client, channel, title, text)

        client.send_message.assert_called_once()


class TestNotifierIntegration:
    """Integration tests for notifier module."""

    @pytest.mark.asyncio
    async def test_multiple_notifications(self):
        """Test sending multiple notifications."""
        client = AsyncMock()

        await notify_dm(client, "Title 1", "Text 1")
        await notify_dm(client, "Title 2", "Text 2")
        await notify_channel(client, "@channel", "Title 3", "Text 3")

        assert client.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_dm_and_channel_different_targets(self):
        """Test that DM and channel notifications go to different targets."""
        client = AsyncMock()

        await notify_dm(client, "DM Title", "DM Text")
        await notify_channel(client, "@channel", "Channel Title", "Channel Text")

        calls = client.send_message.call_args_list
        assert calls[0][0][0] == "me"
        assert calls[1][0][0] == "@channel"
