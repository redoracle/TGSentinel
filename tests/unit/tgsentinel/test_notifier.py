"""Unit tests for notifier module."""

from unittest.mock import AsyncMock

import pytest

from tgsentinel.notifier import notify_dm

# NOTE: notify_channel tests removed - function was deprecated and removed
# as part of legacy code cleanup. Channel notifications should now use
# notify_dm with the user's preferred delivery configuration.


@pytest.mark.unit
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
        """Test that notify_dm formats the message with template rendering."""
        client = AsyncMock()
        title = "Important Channel"
        text = "Critical alert"

        await notify_dm(client, title, text)

        # Message now uses templates, so check that it was called with correct target
        # and that the message contains the essential elements
        client.send_message.assert_called_once()
        call_args = client.send_message.call_args[0]
        assert call_args[0] == "me"  # target
        message = call_args[1]
        assert title in message
        assert text in message
        assert "🔔" in message  # Alert emoji should be in template

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


# NOTE: TestNotifyChannel class removed - notify_channel function was
# deprecated and removed as part of legacy code cleanup.


@pytest.mark.unit
class TestNotifierIntegration:
    """Integration tests for notifier module."""

    @pytest.mark.asyncio
    async def test_multiple_notifications(self):
        """Test sending multiple notifications."""
        client = AsyncMock()

        await notify_dm(client, "Title 1", "Text 1")
        await notify_dm(client, "Title 2", "Text 2")
        await notify_dm(client, "Title 3", "Text 3")

        assert client.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_dm_notifications_all_go_to_me(self):
        """Test that all DM notifications go to 'me'."""
        client = AsyncMock()

        await notify_dm(client, "DM Title 1", "DM Text 1")
        await notify_dm(client, "DM Title 2", "DM Text 2")

        calls = client.send_message.call_args_list
        assert calls[0][0][0] == "me"
        assert calls[1][0][0] == "me"
