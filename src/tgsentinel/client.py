import asyncio
import json
import logging
import os

from redis import Redis
from telethon import TelegramClient, events

from .config import AppCfg

log = logging.getLogger(__name__)


def make_client(cfg: AppCfg) -> TelegramClient:
    client = TelegramClient(cfg.telegram_session, cfg.api_id, cfg.api_hash)
    return client


def _reaction_count(msg) -> int:
    rs = getattr(msg, "reactions", None)
    if not rs or not rs.results:
        return 0
    return sum([r.count for r in rs.results])


def start_ingestion(cfg: AppCfg, client: TelegramClient, r: Redis):
    stream = cfg.redis["stream"]

    listener = events.NewMessage()

    async def handler(event):
        m = event.message
        try:
            payload = {
                "chat_id": event.chat_id,
                "chat_title": getattr(event.chat, "title", "") if event.chat else "",
                "msg_id": m.id,
                "sender_id": m.sender_id,
                "mentioned": bool(m.mentioned),
                "text": (m.message or ""),
                "replies": int(m.replies.replies if m.replies is not None else 0),
                "reactions": _reaction_count(m),
            }
            r.xadd(
                stream, {"json": json.dumps(payload)}, maxlen=100000, approximate=True
            )
        except Exception as e:
            log.exception("ingest_error: %s", e)

    on_method = getattr(client, "on", None)
    registered = False
    if callable(on_method):
        decorator = on_method(listener)
        if callable(decorator):
            decorator(handler)
            registered = True
        elif asyncio.iscoroutine(decorator):
            decorator.close()

    if not registered:
        add_handler = getattr(client, "add_event_handler", None)
        if callable(add_handler):
            maybe_coro = add_handler(handler, listener)
            if asyncio.iscoroutine(maybe_coro):
                maybe_coro.close()
            registered = True

    if not registered:
        raise RuntimeError("Could not register ingestion handler")

    return client
