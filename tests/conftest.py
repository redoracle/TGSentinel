"""Test configuration and shared fixtures for TG Sentinel tests."""

import fnmatch
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class InMemoryRedis:
    """Lightweight in-memory Redis replacement for tests.

    Provides just enough behaviour for the test-suite without requiring a real
    Redis server or network access.
    """

    def __init__(self, *args, **kwargs):
        self._data: dict[str, object] = {}
        self._streams: dict[str, list[tuple[str, dict[str, object]]]] = {}

    # Basic connection/housekeeping -------------------------------------------------
    def ping(self) -> bool:  # type: ignore[override]
        return True

    def close(self) -> None:  # type: ignore[override]
        return None

    def flushall(self) -> None:  # type: ignore[override]
        self._data.clear()
        self._streams.clear()

    # Key/value operations ----------------------------------------------------------
    def set(self, key: str, value: object, ex: int | None = None) -> bool:  # type: ignore[override]
        self._data[key] = value
        return True

    def setex(self, key: str, ttl: int, value: object) -> bool:  # type: ignore[override]
        return self.set(key, value)

    def get(self, key: str) -> object | None:  # type: ignore[override]
        return self._data.get(key)

    def delete(self, *keys: str) -> int:  # type: ignore[override]
        removed = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                removed += 1
            if key in self._streams:
                del self._streams[key]
                removed += 1
        return removed

    def exists(self, key: str) -> int:  # type: ignore[override]
        return int(key in self._data or key in self._streams)

    def keys(self, pattern: str = "*"):  # type: ignore[override]
        all_keys = list(self._data.keys()) + list(self._streams.keys())
        return [k for k in all_keys if fnmatch.fnmatch(str(k), pattern)]

    def scan_iter(self, pattern: str = "*"):  # type: ignore[override]
        for key in self.keys(pattern):
            yield key

    # List operations ---------------------------------------------------------------
    def rpush(self, key: str, *values: object) -> int:  # type: ignore[override]
        lst = self._data.get(key)
        if not isinstance(lst, list):
            lst = []
        lst.extend(values)
        self._data[key] = lst
        return len(lst)

    # Hash operations ---------------------------------------------------------------
    def hget(self, name: str, key: str) -> object | None:  # type: ignore[override]
        h = self._data.get(name)
        if not isinstance(h, dict):
            return None
        return h.get(key)

    def hset(self, name: str, key: str, value: object) -> int:  # type: ignore[override]
        h = self._data.get(name)
        if not isinstance(h, dict):
            h = {}
        h[key] = value
        self._data[name] = h
        return 1

    def hdel(self, name: str, *keys: str) -> int:  # type: ignore[override]
        h = self._data.get(name)
        if not isinstance(h, dict):
            return 0
        removed = 0
        for key in keys:
            if key in h:
                del h[key]
                removed += 1
        return removed

    # Stream operations -------------------------------------------------------------
    def xadd(self, stream: str, fields: dict[str, object], *_, **__) -> str:  # type: ignore[override]
        items = self._streams.setdefault(stream, [])
        if items:
            last = items[-1][0]
            try:
                base = int(str(last).split("-")[0])
            except Exception:
                base = len(items)
            new_id = f"{base + 1}-0"
        else:
            new_id = "1-0"
        items.append((new_id, fields))
        return new_id

    def xlen(self, stream: str) -> int:  # type: ignore[override]
        return len(self._streams.get(stream, []))

    def xrevrange(
        self, stream: str, max: str = "+", min: str = "-", count: int | None = None
    ):  # type: ignore[override]
        items = list(reversed(self._streams.get(stream, [])))
        return items if count is None else items[:count]

    def xrange(
        self, stream: str, min: str = "-", max: str = "+", count: int | None = None
    ):  # type: ignore[override]
        items = list(self._streams.get(stream, []))
        return items if count is None else items[:count]


# Provide compatible shims for optional dependencies when running in
# minimal environments (e.g. local tooling without full requirements).
# In normal development/CI, the real packages from requirements.txt
# will be available and these fallbacks are skipped.
try:  # pragma: no cover - environment shim
    import redis  # type: ignore
    from redis import Redis  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import types

    redis = types.ModuleType("redis")
    # Use the in-memory implementation as a lightweight stand‑in so
    # tests can exercise logic without a real Redis server.
    redis.Redis = InMemoryRedis  # type: ignore[attr-defined]
    sys.modules["redis"] = redis
    from redis import Redis  # type: ignore  # re-import from shim

try:  # pragma: no cover - environment shim
    import telethon  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import types

    telethon = types.ModuleType("telethon")

    class _DummyTelegramClient:  # minimal placeholder
        def __init__(self, *_, **__):
            pass

    class _DummyEvents:
        class NewMessage:
            def __init__(self, *_, **__):
                pass

    telethon.TelegramClient = _DummyTelegramClient  # type: ignore[attr-defined]
    telethon.events = _DummyEvents  # type: ignore[attr-defined]
    sys.modules["telethon"] = telethon

try:  # pragma: no cover - environment shim
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    import types

    yaml = types.ModuleType("yaml")

    def _safe_load(_stream):
        # Minimal placeholder: return empty config so code paths that
        # rely on explicit overrides in tests can still construct AppCfg.
        return {}

    yaml.safe_load = _safe_load  # type: ignore[attr-defined]
    sys.modules["yaml"] = yaml


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_config_file(temp_dir):
    """Create a temporary config file for testing."""
    config_content = """
telegram:
  session: "data/test.session"

alerts:
  mode: "dm"
  target_channel: ""
  digest:
    hourly: true
    daily: true
    top_n: 10

channels:
  - id: -100123456789
    name: "Test Channel"
    vip_senders: [11111, 22222]
    keywords: ["test", "important"]
    reaction_threshold: 5
    reply_threshold: 3
    rate_limit_per_hour: 10

interests:
  - "test topic"
  - "important subject"
"""
    config_file = temp_dir / "test_config.yml"
    config_file.write_text(config_content)
    return str(config_file)


@pytest.fixture
def test_env_vars(monkeypatch):
    """Set up test environment variables."""
    monkeypatch.setenv("TG_API_ID", "123456")
    monkeypatch.setenv("TG_API_HASH", "test_hash_123")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("DB_URI", "sqlite:///:memory:")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "")
    monkeypatch.setenv("SIMILARITY_THRESHOLD", "0.42")


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    # Use a plain MagicMock here instead of spec=Redis so tests remain
    # usable even when a lightweight Redis shim is active in local
    # tooling environments.
    redis_mock = MagicMock()
    redis_mock.xadd.return_value = b"1234567890-0"
    redis_mock.xreadgroup.return_value = []
    redis_mock.xgroup_create.return_value = True
    redis_mock.xack.return_value = 1
    return redis_mock


@pytest.fixture
def mock_telegram_client():
    """Create a mock Telegram client."""
    client = AsyncMock()
    client.send_message = AsyncMock()
    client.start = AsyncMock()
    client.run_until_disconnected = AsyncMock()
    return client


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database for testing."""
    try:
        from tgsentinel.store import init_db
    except ModuleNotFoundError:  # pragma: no cover - optional in lightweight envs
        pytest.skip("tgsentinel.store is not available in this environment")

    engine = init_db("sqlite:///:memory:")
    return engine


@pytest.fixture
def sample_message_payload():
    """Create a sample message payload for testing."""
    return {
        "chat_id": -100123456789,
        "chat_title": "Test Channel",
        "msg_id": 12345,
        "sender_id": 11111,
        "mentioned": False,
        "text": "This is a test message with important keyword",
        "replies": 2,
        "reactions": 5,
    }


@pytest.fixture
def sample_telegram_message():
    """Create a sample Telegram message object."""
    msg = MagicMock()
    msg.id = 12345
    msg.sender_id = 11111
    msg.mentioned = False
    msg.message = "Test message"
    msg.replies = MagicMock()
    msg.replies.replies = 2
    msg.reactions = MagicMock()
    msg.reactions.results = [MagicMock(count=3), MagicMock(count=2)]
    return msg


@pytest.fixture
def sample_telegram_event():
    """Create a sample Telegram event."""
    event = MagicMock()
    event.chat_id = -100123456789

    # Mock chat
    chat = MagicMock()
    chat.title = "Test Channel"
    event.chat = chat

    # Mock async get_chat() method
    async def mock_get_chat():
        return chat

    event.get_chat = mock_get_chat

    # Mock sender with all required attributes (id, first_name, last_name, username)
    sender = MagicMock()
    sender.id = 11111
    sender.first_name = "John"
    sender.last_name = "Doe"
    sender.username = "johndoe"
    event.sender = sender

    # Mock async get_sender() method
    async def mock_get_sender():
        return sender

    event.get_sender = mock_get_sender

    event.message = MagicMock()
    event.message.id = 12345
    event.message.sender_id = 11111  # Matches sender.id
    event.message.mentioned = False
    event.message.message = "Test message"
    event.message.replies = MagicMock()
    event.message.replies.replies = 2
    event.message.reactions = None
    # Add date attribute for timestamp field
    from datetime import datetime, timezone

    event.message.date = datetime.now(timezone.utc)
    return event


@pytest.fixture
def app():
    """Create Flask app instance for testing."""
    ui_path = Path(__file__).parent.parent / "ui"
    sys.path.insert(0, str(ui_path))

    # Set test environment variables
    os.environ["UI_DB_URI"] = "sqlite:///:memory:"
    os.environ["UI_SECRET_KEY"] = "test-secret-key"
    os.environ["TG_API_ID"] = "123456"
    os.environ["TG_API_HASH"] = "test_hash"

    # Remove cached app module to force fresh import
    if "app" in sys.modules:
        del sys.modules["app"]

    # Create mock config
    mock_config = MagicMock()
    mock_config.channels = []
    mock_config.db_uri = "sqlite:///:memory:"
    mock_config.redis = {
        "host": "localhost",
        "port": 6379,
        "db": 15,  # Use DB 15 for tests
        "stream": "sentinel:messages",
    }
    mock_alerts = MagicMock()
    mock_alerts.mode = "dm"
    mock_config.alerts = mock_alerts

    with patch("app.load_config", return_value=mock_config):
        import app as flask_app  # type: ignore[import-not-found]

        # Reset module state for test isolation
        flask_app.reset_for_testing()

        flask_app.app.config["TESTING"] = True
        flask_app.app.config["TGSENTINEL_CONFIG"] = mock_config

        # Initialize app to register blueprints
        flask_app.init_app()

        yield flask_app.app

    # Cleanup
    if "UI_DB_URI" in os.environ:
        del os.environ["UI_DB_URI"]
    if "UI_SECRET_KEY" in os.environ:
        del os.environ["UI_SECRET_KEY"]
    if "TG_API_ID" in os.environ:
        del os.environ["TG_API_ID"]
    if "TG_API_HASH" in os.environ:
        del os.environ["TG_API_HASH"]


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_init():
    """Mock initialization to avoid external dependencies."""
    with (
        patch("ui.app._is_initialized", True),
        patch("ui.app.config") as mock_config,
        patch("ui.app.engine") as mock_engine,
        patch("ui.app.redis_client"),
    ):
        mock_config.telegram_session = "/app/data/test.session"
        mock_config.db_uri = "sqlite:///:memory:"
        mock_config.redis = {"host": "localhost", "port": 6379}

        # Mock database connection
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.scalar.return_value = 0
        mock_conn.execute.return_value = []

        yield mock_config
