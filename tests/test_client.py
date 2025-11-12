"""Unit tests for client module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tgsentinel.client import _reaction_count, make_client, start_ingestion
from tgsentinel.config import AlertsCfg, AppCfg, DigestCfg


class TestMakeClient:
    """Test client creation."""

    def test_make_client_creates_telethon_client(self):
        """Test that make_client creates a TelegramClient."""
        cfg = AppCfg(
            telegram_session="test.session",
            api_id=123456,
            api_hash="test_hash",
            alerts=AlertsCfg(),
            channels=[],
            interests=[],
            redis={},
            db_uri="sqlite:///:memory:",
            embeddings_model=None,
            similarity_threshold=0.42,
        )

        with patch("tgsentinel.client.TelegramClient") as mock_client:
            client = make_client(cfg)

            mock_client.assert_called_once_with("test.session", 123456, "test_hash")


class TestReactionCount:
    """Test reaction counting."""

    def test_reaction_count_no_reactions(self):
        """Test counting when there are no reactions."""
        msg = MagicMock()
        msg.reactions = None

        count = _reaction_count(msg)

        assert count == 0

    def test_reaction_count_empty_results(self):
        """Test counting when reactions exist but results are empty."""
        msg = MagicMock()
        msg.reactions = MagicMock()
        msg.reactions.results = []

        count = _reaction_count(msg)

        assert count == 0

    def test_reaction_count_single_reaction(self):
        """Test counting a single reaction type."""
        msg = MagicMock()
        msg.reactions = MagicMock()
        reaction = MagicMock()
        reaction.count = 5
        msg.reactions.results = [reaction]

        count = _reaction_count(msg)

        assert count == 5

    def test_reaction_count_multiple_reactions(self):
        """Test counting multiple reaction types."""
        msg = MagicMock()
        msg.reactions = MagicMock()
        reaction1 = MagicMock()
        reaction1.count = 3
        reaction2 = MagicMock()
        reaction2.count = 7
        reaction3 = MagicMock()
        reaction3.count = 2
        msg.reactions.results = [reaction1, reaction2, reaction3]

        count = _reaction_count(msg)

        assert count == 12

    def test_reaction_count_no_reactions_attribute(self):
        """Test counting when message has no reactions attribute."""
        msg = MagicMock(spec=[])  # No reactions attribute

        count = _reaction_count(msg)

        assert count == 0


class TestStartIngestion:
    """Test ingestion setup."""

    @pytest.mark.asyncio
    async def test_start_ingestion_registers_handler(
        self, mock_redis, mock_telegram_client
    ):
        """Test that start_ingestion registers a message handler."""
        cfg = AppCfg(
            telegram_session="test.session",
            api_id=123456,
            api_hash="test_hash",
            alerts=AlertsCfg(),
            channels=[],
            interests=[],
            redis={"stream": "test:stream"},
            db_uri="sqlite:///:memory:",
            embeddings_model=None,
            similarity_threshold=0.42,
        )

        with patch("tgsentinel.client.events") as mock_events:
            result = start_ingestion(cfg, mock_telegram_client, mock_redis)

            # Verify handler was registered
            mock_telegram_client.on.assert_called()
            assert result == mock_telegram_client

    @pytest.mark.asyncio
    async def test_handler_processes_message(self, mock_redis, sample_telegram_event):
        """Test that the handler processes messages correctly."""
        cfg = AppCfg(
            telegram_session="test.session",
            api_id=123456,
            api_hash="test_hash",
            alerts=AlertsCfg(),
            channels=[],
            interests=[],
            redis={"stream": "test:stream"},
            db_uri="sqlite:///:memory:",
            embeddings_model=None,
            similarity_threshold=0.42,
        )

        client = AsyncMock()
        handler_called = False
        captured_payload = None

        def mock_xadd(stream, fields, **kwargs):
            nonlocal handler_called, captured_payload
            handler_called = True
            captured_payload = json.loads(fields["json"])
            return b"1234567890-0"

        mock_redis.xadd = mock_xadd

        # Register handler
        registered_handlers = []

        def mock_on(event_type):
            def decorator(func):
                registered_handlers.append(func)
                return func

            return decorator

        client.on = mock_on

        start_ingestion(cfg, client, mock_redis)

        # Call the registered handler
        if registered_handlers:
            await registered_handlers[0](sample_telegram_event)

        # Verify handler was called and payload was captured
        assert handler_called
        assert captured_payload is not None
        assert captured_payload["chat_id"] == -100123456789
        assert captured_payload["msg_id"] == 12345
        assert captured_payload["sender_id"] == 11111
        assert captured_payload["mentioned"] is False
        assert captured_payload["text"] == "Test message"

    @pytest.mark.asyncio
    async def test_handler_counts_replies_safely(self, mock_redis):
        """Test that handler safely handles None replies."""
        cfg = AppCfg(
            telegram_session="test.session",
            api_id=123456,
            api_hash="test_hash",
            alerts=AlertsCfg(),
            channels=[],
            interests=[],
            redis={"stream": "test:stream"},
            db_uri="sqlite:///:memory:",
            embeddings_model=None,
            similarity_threshold=0.42,
        )

        client = AsyncMock()

        # Create event with None replies
        event = MagicMock()
        event.chat_id = -100123456789
        event.chat = MagicMock()
        event.chat.title = "Test"
        event.message = MagicMock()
        event.message.id = 12345
        event.message.sender_id = 11111
        event.message.mentioned = False
        event.message.message = "Test"
        event.message.replies = None  # This is the key test
        event.message.reactions = None

        registered_handlers = []

        def mock_on(event_type):
            def decorator(func):
                registered_handlers.append(func)
                return func

            return decorator

        client.on = mock_on

        captured_payload = None

        def mock_xadd(stream, fields, **kwargs):
            nonlocal captured_payload
            captured_payload = json.loads(fields["json"])
            return b"1234567890-0"

        mock_redis.xadd = mock_xadd

        start_ingestion(cfg, client, mock_redis)

        # Call the handler
        if registered_handlers:
            await registered_handlers[0](event)

        # Verify replies is 0 when None
        assert captured_payload is not None
        assert captured_payload["replies"] == 0

    @pytest.mark.asyncio
    async def test_handler_exception_handling(self, mock_redis, caplog):
        """Test that handler exceptions are caught and logged."""
        cfg = AppCfg(
            telegram_session="test.session",
            api_id=123456,
            api_hash="test_hash",
            alerts=AlertsCfg(),
            channels=[],
            interests=[],
            redis={"stream": "test:stream"},
            db_uri="sqlite:///:memory:",
            embeddings_model=None,
            similarity_threshold=0.42,
        )

        client = AsyncMock()

        # Create event that will cause an exception
        event = MagicMock()
        event.chat_id = None  # This will cause issues

        registered_handlers = []

        def mock_on(event_type):
            def decorator(func):
                registered_handlers.append(func)
                return func

            return decorator

        client.on = mock_on

        start_ingestion(cfg, client, mock_redis)

        # Call the handler - should not raise exception
        if registered_handlers:
            await registered_handlers[0](event)

        # Verify exception was logged
        assert "ingest_error" in caplog.text
