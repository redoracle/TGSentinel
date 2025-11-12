import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tgsentinel.client import start_ingestion
from tgsentinel.config import AlertsCfg, AppCfg, ChannelRule, DigestCfg
from tgsentinel.store import init_db
from tgsentinel.worker import process_loop


class FakeRedis:
    def __init__(self, processed: asyncio.Event):
        self.processed = processed
        self.stream = []
        self.acks = []

    def xgroup_create(
        self, stream: str, group: str, id: str = "$", mkstream: bool = False
    ):
        return True

    def xadd(self, stream: str, fields: dict[str, Any], maxlen=None, approximate=None):
        msg_id = f"{len(self.stream) + 1}-0"
        self.stream.append((msg_id, fields))
        return msg_id

    def xreadgroup(self, group: str, consumer: str, streams, count=50, block=0):
        if not self.stream:
            return []
        pending = self.stream[:count]
        self.stream = self.stream[count:]
        stream_name = next(iter(streams.keys()))
        return [(stream_name, pending)]

    def xack(self, stream: str, group: str, msg_id: str):
        self.acks.append(msg_id)
        self.processed.set()
        return 1


class FakeTelegramClient:
    def __init__(self):
        self.handlers = []
        self.sent = []

    def on(self, event):
        def decorator(func):
            self.handlers.append(func)
            return func

        return decorator

    async def send_message(self, target: str, text: str):
        self.sent.append((target, text))


def build_config() -> AppCfg:
    channel = ChannelRule(
        id=-100123,
        name="Test Channel",
        keywords=[],
        vip_senders=[],
        reaction_threshold=1,
        reply_threshold=0,
        rate_limit_per_hour=5,
    )
    return AppCfg(
        telegram_session="data/test.session",
        api_id=123456,
        api_hash="hash",
        alerts=AlertsCfg(digest=DigestCfg(hourly=False, daily=False, top_n=5)),
        channels=[channel],
        interests=[],
        redis={
            "host": "localhost",
            "port": 6379,
            "stream": "tgsentinel:test",
            "group": "workers",
            "consumer": "worker-1",
        },
        db_uri="sqlite:///:memory:",
        embeddings_model=None,
        similarity_threshold=0.1,
    )


@pytest.mark.asyncio
async def test_full_ingest_and_process_pipeline(monkeypatch):
    # Disable embeddings to avoid empty list encoding issue
    monkeypatch.setenv("EMBEDDINGS_MODEL", "")

    cfg = build_config()
    engine = init_db(cfg.db_uri)

    processed_event = asyncio.Event()
    fake_redis = FakeRedis(processed_event)
    fake_client = FakeTelegramClient()

    with patch("tgsentinel.client.events.NewMessage", return_value="new"):
        start_ingestion(cfg, fake_client, fake_redis)  # type: ignore[arg-type]

    assert fake_client.handlers, "Handler should be registered"

    message = SimpleNamespace(
        id=42,
        sender_id=11111,
        mentioned=True,
        message="Critical update",
        replies=SimpleNamespace(replies=2),
        reactions=SimpleNamespace(results=[SimpleNamespace(count=3)]),
    )
    event = SimpleNamespace(
        chat_id=-100123,
        chat=SimpleNamespace(title="Test Channel"),
        message=message,
    )

    await fake_client.handlers[0](event)
    assert fake_redis.stream, "Message should be added to Redis stream"

    with patch("tgsentinel.worker.Redis", return_value=fake_redis):
        task = asyncio.create_task(process_loop(cfg, fake_client, engine))  # type: ignore[arg-type]
        await asyncio.wait_for(processed_event.wait(), timeout=1)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert fake_redis.acks, "Worker should acknowledge processed message"
    assert fake_client.sent, "Notification should be sent"

    with engine.begin() as con:
        row = con.execute(
            text("SELECT alerted, score FROM messages WHERE chat_id=:c"),
            {"c": -100123},
        ).fetchone()
    assert row is not None
    alerted, score = row
    assert alerted == 1
    assert score > 0
