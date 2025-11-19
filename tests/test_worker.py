"""Tests for the worker module."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text as sql_text

import tgsentinel.worker as worker
from tgsentinel.config import AlertsCfg, AppCfg, ChannelRule, DigestCfg
from tgsentinel.heuristics import HeuristicResult


pytestmark = pytest.mark.integration


def _make_cfg(
    *,
    mode: str = "both",
    target_channel: str = "@kit_red_bot",
    similarity_threshold: float = 0.4,
    channels: list[ChannelRule] | None = None,
) -> AppCfg:
    return AppCfg(
        telegram_session="sess",
        api_id=123,
        api_hash="hash",
        alerts=AlertsCfg(mode=mode, target_channel=target_channel, digest=DigestCfg()),
        channels=channels
        or [
            ChannelRule(
                id=-1001,
                name="Primary",
                vip_senders=[42],
                keywords=["important"],
                reaction_threshold=2,
                reply_threshold=1,
                rate_limit_per_hour=10,
            )
        ],
        monitored_users=[],
        interests=["test topic"],
        redis={
            "host": "localhost",
            "port": 6379,
            "stream": "tgsentinel:messages",
            "group": "workers",
            "consumer": "worker-1",
        },
        db_uri="sqlite:///:memory:",
        embeddings_model=None,
        similarity_threshold=similarity_threshold,
    )


def _row_value(engine, chat_id: int, msg_id: int, column: str) -> Any:
    with engine.connect() as conn:
        res = conn.execute(
            sql_text(
                "SELECT {column} FROM messages WHERE chat_id=:c AND msg_id=:m".format(
                    column=column
                )
            ),
            {"c": chat_id, "m": msg_id},
        ).fetchone()
    return res[0] if res else None


@pytest.mark.asyncio
async def test_process_stream_message_semantic_alert(monkeypatch, in_memory_db):
    cfg = _make_cfg(similarity_threshold=0.3)
    rules = worker.load_rules(cfg)
    client = AsyncMock()

    heur = HeuristicResult(
        important=False,
        reasons=[],
        content_hash="hash123",
        pre_score=0.1,
    )
    monkeypatch.setattr(
        "tgsentinel.worker.run_heuristics", lambda *args, **kwargs: heur
    )
    monkeypatch.setattr("tgsentinel.worker.score_text", lambda text: 0.5)

    notify_dm = AsyncMock()
    notify_channel = AsyncMock()
    monkeypatch.setattr("tgsentinel.worker.notify_dm", notify_dm)
    monkeypatch.setattr("tgsentinel.worker.notify_channel", notify_channel)

    inc_calls = []

    def fake_inc(metric: str, **labels):
        inc_calls.append((metric, labels))

    monkeypatch.setattr("tgsentinel.worker.inc", fake_inc)

    payload = {
        "chat_id": "-1001",
        "chat_title": "Primary",
        "msg_id": "77",
        "sender_id": "555",
        "mentioned": False,
        "text": "Deep dive on important topic",
        "replies": "0",
        "reactions": "1",
    }

    result = await worker.process_stream_message(
        cfg=cfg,
        client=client,
        engine=in_memory_db,
        rules=rules,
        payload=payload,
    )

    assert result is True
    assert notify_dm.await_count == 1
    assert notify_channel.await_count == 1
    assert notify_channel.await_args_list[0].args[1] == "@kit_red_bot"
    assert inc_calls == [("alerts_total", {"chat": -1001})]
    assert _row_value(in_memory_db, -1001, 77, "alerted") == 1


@pytest.mark.asyncio
async def test_process_stream_message_skips_when_not_important(
    monkeypatch, in_memory_db
):
    cfg = _make_cfg()
    rules = worker.load_rules(cfg)
    client = AsyncMock()

    heur = HeuristicResult(
        important=False,
        reasons=[],
        content_hash="hash456",
        pre_score=0.0,
    )
    monkeypatch.setattr(
        "tgsentinel.worker.run_heuristics", lambda *args, **kwargs: heur
    )
    monkeypatch.setattr("tgsentinel.worker.score_text", lambda text: None)

    notify_dm = AsyncMock()
    notify_channel = AsyncMock()
    monkeypatch.setattr("tgsentinel.worker.notify_dm", notify_dm)
    monkeypatch.setattr("tgsentinel.worker.notify_channel", notify_channel)

    inc_calls = []

    def fake_inc(metric: str, **labels):
        inc_calls.append((metric, labels))

    monkeypatch.setattr("tgsentinel.worker.inc", fake_inc)

    payload = {
        "chat_id": -1001,
        "chat_title": "Primary",
        "msg_id": 88,
        "sender_id": 12,
        "mentioned": False,
        "text": "Routine status update",
        "replies": 0,
        "reactions": 0,
    }

    result = await worker.process_stream_message(
        cfg=cfg,
        client=client,
        engine=in_memory_db,
        rules=rules,
        payload=payload,
    )

    assert result is False
    assert notify_dm.await_count == 0
    assert notify_channel.await_count == 0
    assert inc_calls == []
    assert _row_value(in_memory_db, -1001, 88, "alerted") == 0


def test_to_int_conversions():
    assert worker._to_int("42") == 42
    assert worker._to_int(True) == 1
    assert worker._to_int(7.9) == 7
    assert worker._to_int(None, default=3) == 3
    with pytest.raises(TypeError):
        worker._to_int(object())


def test_load_rules_maps_chat_ids():
    channels = [ChannelRule(id=1, name="One"), ChannelRule(id=2, name="Two")]
    cfg = _make_cfg(channels=channels)
    rules = worker.load_rules(cfg)
    assert list(rules) == [1, 2]
    assert rules[1].name == "One"


class _RedisHarness:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.read_calls = 0
        self.acked: list[tuple[str, str, str]] = []

    def xgroup_create(self, *args, **kwargs):
        return True

    def xreadgroup(self, *args, **kwargs):
        self.read_calls += 1
        if self.read_calls == 1:
            return [("stream", [("1-0", {"json": json.dumps(self.payload)})])]
        return []

    def xack(self, stream, group, msg_id):
        self.acked.append((stream, group, msg_id))
        return 1


@pytest.mark.asyncio
async def test_process_loop_acks_and_tracks(monkeypatch):
    payload = {
        "chat_id": 1,
        "chat_title": "Loop",
        "msg_id": 5,
        "sender_id": 7,
        "mentioned": False,
        "text": "Check",
        "replies": 0,
        "reactions": 0,
    }
    harness = _RedisHarness(payload)
    monkeypatch.setattr("tgsentinel.worker.Redis", lambda **kwargs: harness)

    inc_calls = []

    def fake_inc(metric: str, **labels):
        inc_calls.append((metric, labels))

    monkeypatch.setattr("tgsentinel.worker.inc", fake_inc)
    monkeypatch.setattr("tgsentinel.worker.load_interests", lambda interests: None)

    process_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("tgsentinel.worker.process_stream_message", process_mock)

    async def fake_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr("tgsentinel.worker.asyncio.sleep", fake_sleep)

    cfg = _make_cfg()
    client = AsyncMock()
    engine = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await worker.process_loop(cfg, client, engine)

    assert harness.acked == [("tgsentinel:messages", "workers", "1-0")]
    assert inc_calls[-1] == ("processed_total", {"important": True})
    process_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_loop_records_errors(monkeypatch):
    payload = {
        "chat_id": 1,
        "chat_title": "Loop",
        "msg_id": 5,
        "sender_id": 7,
        "mentioned": False,
        "text": "Check",
        "replies": 0,
        "reactions": 0,
    }
    harness = _RedisHarness(payload)
    monkeypatch.setattr("tgsentinel.worker.Redis", lambda **kwargs: harness)
    monkeypatch.setattr("tgsentinel.worker.load_interests", lambda interests: None)

    inc_calls = []

    def fake_inc(metric: str, **labels):
        inc_calls.append((metric, labels))

    monkeypatch.setattr("tgsentinel.worker.inc", fake_inc)

    async def failing_process(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("tgsentinel.worker.process_stream_message", failing_process)

    async def fake_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr("tgsentinel.worker.asyncio.sleep", fake_sleep)

    cfg = _make_cfg()
    client = AsyncMock()
    engine = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await worker.process_loop(cfg, client, engine)

    assert harness.acked == []
    assert ("errors_total", {}) in inc_calls
