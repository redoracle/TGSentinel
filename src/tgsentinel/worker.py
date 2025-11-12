import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple, cast

from redis import Redis
from telethon import TelegramClient

from .config import AppCfg, ChannelRule, load_config
from .heuristics import run_heuristics
from .metrics import inc
from .notifier import notify_channel, notify_dm
from .semantic import load_interests, score_text
from .store import mark_alerted, upsert_message

log = logging.getLogger(__name__)

StreamEntry = Tuple[str, Dict[str, str]]
StreamResponse = List[Tuple[str, List[StreamEntry]]]


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default
        return int(value)
    raise TypeError(f"Unsupported value type: {type(value)!r}")


def load_rules(cfg: AppCfg):
    rule_by_chat = {}
    for c in cfg.channels:
        rule_by_chat[c.id] = c
    return rule_by_chat


async def process_stream_message(
    cfg: AppCfg,
    client: TelegramClient,
    engine,
    rules: Dict[int, ChannelRule],
    payload: Dict[str, Any],
) -> bool:
    rid = _to_int(payload["chat_id"])
    rule = rules.get(rid)
    vip = set(rule.vip_senders) if rule else set()
    keywords = rule.keywords if rule else []
    msg_id = _to_int(payload["msg_id"])
    hr = run_heuristics(
        text=str(payload["text"]),
        sender_id=_to_int(payload.get("sender_id"), 0),
        mentioned=bool(payload["mentioned"]),
        reactions=_to_int(payload["reactions"]),
        replies=_to_int(payload["replies"]),
        vip=vip,
        keywords=keywords,
        react_thr=(rule.reaction_threshold if rule else 0),
        reply_thr=(rule.reply_threshold if rule else 0),
    )

    score = hr.pre_score
    sem = score_text(str(payload["text"]))
    if sem is not None:
        score += sem
    upsert_message(
        engine,
        rid,
        msg_id,
        hr.content_hash,
        score,
    )

    important = hr.important or (sem is not None and sem >= cfg.similarity_threshold)
    if important:
        title = str(payload.get("chat_title") or f"chat {rid}")
        preview = str(payload["text"] or "").strip().replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "…"
        if cfg.alerts.mode in ("dm", "both"):
            await notify_dm(client, title, preview)
        if cfg.alerts.mode in ("channel", "both") and cfg.alerts.target_channel:
            await notify_channel(client, cfg.alerts.target_channel, title, preview)
        mark_alerted(engine, rid, msg_id)
        inc("alerts_total", chat=rid)

    return important


async def process_loop(cfg: AppCfg, client: TelegramClient, engine):
    r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)
    stream = cfg.redis["stream"]
    group = cfg.redis["group"]
    consumer = cfg.redis["consumer"]

    try:
        r.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception:
        pass  # already exists

    rules = load_rules(cfg)
    load_interests(cfg.interests)

    reload_marker = Path("/app/data/.reload_config")
    last_cfg_check = 0
    cfg_check_interval = 5  # Check every 5 seconds

    while True:
        # Check for config reload marker periodically
        current_time = asyncio.get_event_loop().time()
        if current_time - last_cfg_check > cfg_check_interval:
            last_cfg_check = current_time
            if reload_marker.exists():
                try:
                    log.info("Config reload requested, reloading configuration...")
                    new_cfg = load_config()
                    cfg = new_cfg
                    rules = load_rules(cfg)
                    load_interests(cfg.interests)
                    reload_marker.unlink()
                    log.info(
                        "Configuration reloaded successfully with %d channels",
                        len(cfg.channels),
                    )
                    for ch in cfg.channels:
                        log.info("  • %s (id: %d)", ch.name, ch.id)
                except Exception as reload_exc:
                    log.error("Failed to reload configuration: %s", reload_exc)
                    # Remove marker even on failure to prevent infinite retry
                    try:
                        reload_marker.unlink()
                    except Exception:
                        pass

        resp = cast(
            StreamResponse,
            r.xreadgroup(group, consumer, streams={stream: ">"}, count=50, block=5000),
        )
        if not resp:
            await asyncio.sleep(0.1)
            continue
        for _, messages in resp:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields["json"])
                    important = await process_stream_message(
                        cfg, client, engine, rules, payload
                    )
                    r.xack(stream, group, msg_id)
                    inc("processed_total", important=important)
                except Exception as e:
                    inc("errors_total")
                    log.exception("worker_error: %s", e)
                    # do not ack; will be retried
