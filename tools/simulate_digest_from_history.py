#!/usr/bin/env python3
"""
Simulate a digest using historical messages that were populated into Redis.

This utility reads recent entries from the configured Redis stream, scores them
with the same heuristics used by the worker (but without sending per‑message
alerts), writes important ones into the DB as alerted, and finally composes and
sends a digest to the destination configured in .env (ALERT_MODE/ALERT_CHANNEL).

Typical flow for local testing:
  1) Run tools/populate_history.py to seed the Redis stream from Telegram history
  2) Run this script to score + mark messages and send a digest

Usage:
  python tools/simulate_digest_from_history.py \
      [--hours 24] [--top-n 10] [--limit 500] [--stream tgsentinel:messages]

Notes:
  - This script does NOT send immediate per‑message notifications while scoring.
    It only marks important rows and then sends a single digest.
  - Requires TG_API_ID, TG_API_HASH, and ALERT_* env vars to be set or provided
    via config/tgsentinel.yml where applicable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Make local src importable when run outside the container
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from redis import Redis
from telethon import TelegramClient

from tgsentinel.client import make_client
from tgsentinel.config import AppCfg, load_config
from tgsentinel.digest import send_digest
from tgsentinel.heuristics import run_heuristics
from tgsentinel.semantic import load_interests, score_text
from tgsentinel.store import init_db, mark_alerted, upsert_message


log = logging.getLogger("simulate_digest")


def _to_int(v, default: int = 0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                return default
            return int(v)
    except Exception:
        pass
    return default


def _load_rules(cfg: AppCfg):
    return {c.id: c for c in cfg.channels}


def _score_and_store(cfg: AppCfg, payload: dict, engine) -> bool:
    """Score a single payload, store it in DB, and mark alerted if important.

    This mirrors worker.process_stream_message but deliberately avoids sending
    per‑message notifications.
    """
    rid = _to_int(payload.get("chat_id"))
    msg_id = _to_int(payload.get("msg_id"))

    rules = _load_rules(cfg)
    rule = rules.get(rid)
    vip = set(rule.vip_senders) if rule else set()
    keywords = rule.keywords if rule else []

    hr = run_heuristics(
        text=str(payload.get("text", "")),
        sender_id=_to_int(payload.get("sender_id"), 0),
        mentioned=bool(payload.get("mentioned", False)),
        reactions=_to_int(payload.get("reactions"), 0),
        replies=_to_int(payload.get("replies"), 0),
        vip=vip,
        keywords=keywords,
        react_thr=(rule.reaction_threshold if rule else 0),
        reply_thr=(rule.reply_threshold if rule else 0),
    )

    score = hr.pre_score
    sem = score_text(str(payload.get("text", "")))
    if sem is not None:
        score += sem

    chat_title = str(payload.get("chat_title", ""))
    sender_name = str(payload.get("sender_name", ""))
    message_text = str(payload.get("text", ""))
    triggers = ", ".join(hr.reasons) if hr.reasons else ""
    sender_id = _to_int(payload.get("sender_id"), 0)

    upsert_message(
        engine,
        rid,
        msg_id,
        hr.content_hash,
        score,
        chat_title,
        sender_name,
        message_text,
        triggers,
        sender_id,
    )

    important = hr.important or (sem is not None and sem >= cfg.similarity_threshold)
    if important:
        mark_alerted(engine, rid, msg_id)
    return important


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate and send a digest from history"
    )
    parser.add_argument(
        "--hours", type=int, default=24, help="Lookback period for digest"
    )
    parser.add_argument(
        "--top-n", type=int, default=None, help="Top N messages in digest"
    )
    parser.add_argument(
        "--limit", type=int, default=500, help="Max stream messages to process"
    )
    parser.add_argument(
        "--stream", type=str, default=None, help="Redis stream name override"
    )
    parser.add_argument(
        "--no-process",
        action="store_true",
        help="Skip scoring; use existing DB state only",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    log.info("Loading configuration…")
    cfg = load_config()
    top_n = args.top_n if args.top_n is not None else cfg.alerts.digest.top_n
    stream_name = args.stream or cfg.redis.get("stream", "tgsentinel:messages")

    # Fix database path for local execution (Docker uses /app/data, local uses ./data)
    db_uri = cfg.db_uri
    if db_uri.startswith("sqlite:////app/data/"):
        local_db_path = Path(__file__).parent.parent / "data" / db_uri.split("/")[-1]
        local_db_path.parent.mkdir(parents=True, exist_ok=True)
        db_uri = f"sqlite:///{local_db_path}"
        log.info("Adjusted database path for local execution: %s", local_db_path)

    log.info("Initializing database…")
    engine = init_db(db_uri)

    if not args.no_process:
        # Prepare semantic interests once
        load_interests(cfg.interests)

        log.info("Connecting to Redis at %s:%s", cfg.redis["host"], cfg.redis["port"])
        r = Redis(host=cfg.redis["host"], port=cfg.redis["port"], decode_responses=True)

        # Read latest messages (most recent first), then process in chronological order
        entries = r.xrevrange(stream_name, "+", "-", count=args.limit)
        if entries:
            for _id, fields in reversed(entries):
                try:
                    payload = json.loads(fields.get("json", "{}"))
                except Exception:
                    continue
                _score_and_store(cfg, payload, engine)
        log.info("Processed %d messages from stream '%s'", len(entries), stream_name)
    else:
        log.info("Skipping scoring step (using existing DB state)")

    log.info("Connecting to Telegram…")
    client: TelegramClient = make_client(cfg)

    try:
        await client.start()  # type: ignore[misc]
        log.info(
            "Sending digest: mode=%s channel=%s top_n=%s hours=%s",
            cfg.alerts.mode,
            cfg.alerts.target_channel,
            top_n,
            args.hours,
        )
        await send_digest(
            engine=engine,
            client=client,
            since_hours=args.hours,
            top_n=top_n,
            mode=cfg.alerts.mode,
            channel=cfg.alerts.target_channel,
            channels_config=cfg.channels,
        )
        log.info("✅ Digest sent")
    finally:
        await client.disconnect()  # type: ignore[misc]

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
