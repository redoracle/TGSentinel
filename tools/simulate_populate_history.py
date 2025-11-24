#!/usr/bin/env python3
"""
Populate Redis with historical messages from monitored channels.

This script fetches the latest messages from configured Telegram channels
and populates Redis as if they were just received. Useful for testing
the importance scoring, semantic analysis, and alert generation systems.

Usage:
    python tools/populate_history.py [--limit 100] [--channel-id CHANNEL_ID]
"""

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, cast

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import redis
from telethon import TelegramClient
from telethon.sessions import SQLiteSession, StringSession
from telethon.tl.types import Channel, Chat, User

from tgsentinel.config import load_config
from tgsentinel.logging_setup import setup_logging

# Setup logging
log = logging.getLogger(__name__)


async def fetch_channel_history(
    client: TelegramClient, entity, limit: int
) -> List[Dict[str, Any]]:
    """Fetch historical messages from a channel entity."""
    messages = []
    channel_id = entity.id

    try:
        log.info(
            f"Fetching up to {limit} messages from {type(entity).__name__} {channel_id}..."
        )

        async for message in client.iter_messages(entity, limit=limit):  # type: ignore[arg-type]
            try:
                if not message.message:  # Skip empty messages
                    continue

                # Get sender info - wrap in try/except to handle sender resolution errors
                sender_id = None
                sender_name = "Unknown"

                try:
                    if message.sender:
                        sender_id = message.sender_id
                        if isinstance(message.sender, User):
                            sender_name = message.sender.first_name or "Unknown"
                            if message.sender.last_name:
                                sender_name += f" {message.sender.last_name}"
                        elif isinstance(message.sender, Channel):
                            sender_name = message.sender.title or "Channel"
                        elif isinstance(message.sender, Chat):
                            sender_name = message.sender.title or "Chat"
                    elif message.sender_id:
                        # Sender entity couldn't be resolved, just use ID
                        sender_id = message.sender_id
                        sender_name = f"User {message.sender_id}"
                except Exception as sender_err:
                    # Log but continue - sender info is not critical
                    log.debug(
                        f"Could not resolve sender for message {message.id}: {sender_err}"
                    )
                    sender_id = (
                        message.sender_id if hasattr(message, "sender_id") else 0
                    )
                    sender_name = f"User {sender_id}" if sender_id else "Unknown"

                # Get chat info
                chat_title = "Unknown"
                if isinstance(entity, (Channel, Chat)):
                    # Entity was successfully resolved
                    if isinstance(entity, Channel):
                        chat_title = entity.title or "Channel"
                    elif isinstance(entity, Chat):
                        chat_title = entity.title or "Chat"
                elif hasattr(message, "chat") and message.chat:
                    # Fallback: get title from message.chat
                    chat_title = getattr(message.chat, "title", "Unknown")
                elif isinstance(entity, int):
                    # Entity is just an ID, use it as fallback
                    chat_title = f"Channel {entity}"

                # Count replies (thread replies)
                replies_count = 0
                if hasattr(message, "replies") and message.replies:
                    replies_count = message.replies.replies or 0

                # Count reactions
                reactions_count = 0
                if hasattr(message, "reactions") and message.reactions:
                    reactions_count = sum(r.count for r in message.reactions.results)

                # Build message data structure matching client.py format exactly
                # client.py payload: chat_id, chat_title, msg_id, sender_id, sender_name,
                # mentioned, text, replies, reactions, timestamp
                msg_data = {
                    "chat_id": channel_id,
                    "chat_title": chat_title,
                    "msg_id": message.id,
                    "sender_id": sender_id or 0,
                    "sender_name": sender_name,
                    "mentioned": False,  # Historical messages don't have mention context
                    "text": message.message or "",
                    "replies": replies_count,
                    "reactions": reactions_count,
                    "timestamp": (
                        message.date.isoformat()
                        if message.date
                        else datetime.now(timezone.utc).isoformat()
                    ),
                }

                messages.append(msg_data)

            except Exception as msg_err:
                # Log error for this specific message but continue with others
                log.warning(
                    f"Error processing message {message.id} from {channel_id}: {msg_err}"
                )
                continue

        log.info(f"Fetched {len(messages)} messages from channel {channel_id}")

    except Exception as e:
        log.error(f"Error fetching history from channel {channel_id}: {e}")

    return messages


def populate_redis_stream(redis_client: redis.Redis, messages: list, stream_name: str):
    """
    Populate Redis stream with historical messages.

    Args:
        redis_client: Redis client instance
        messages: List of message dictionaries
        stream_name: Name of the Redis stream
    """
    added_count = 0

    for msg in reversed(messages):  # Add oldest first to maintain chronological order
        try:
            # Match client.py schema: wrap payload in a 'json' field
            # Client publishes: r.xadd(stream, {"json": json.dumps(payload)}, ...)
            redis_data = {"json": json.dumps(msg)}

            # Add to Redis stream
            redis_client.xadd(stream_name, cast(dict[Any, Any], redis_data))
            added_count += 1

        except Exception as e:
            log.error(f"Error adding message {msg.get('msg_id')} to Redis: {e}")

    log.info(f"Added {added_count} messages to Redis stream '{stream_name}'")
    return added_count


async def main():
    parser = argparse.ArgumentParser(
        description="Populate Redis with historical Telegram messages for testing"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Number of messages to fetch per channel (default: 100)",
    )
    parser.add_argument(
        "--channel-id",
        type=int,
        help="Specific channel ID to fetch from (default: all monitored channels)",
    )
    parser.add_argument(
        "--stream",
        type=str,
        default="tgsentinel:messages",
        help="Redis stream name (default: tgsentinel:messages)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear the Redis stream before adding messages",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch messages but don't add to Redis"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    # Load configuration
    log.info("Loading configuration...")
    try:
        cfg = load_config()
    except Exception as e:
        log.error(f"Failed to load configuration: {e}")
        return 1

    # Check for API credentials
    api_id = os.getenv("TG_API_ID") or cfg.api_id
    api_hash = os.getenv("TG_API_HASH") or cfg.api_hash

    if not api_id or not api_hash:
        log.error(
            "Missing TG_API_ID or TG_API_HASH. Set environment variables or check config."
        )
        return 1

    # Connect to Redis
    # Try configured host first, fallback to localhost if connection fails (local dev)
    redis_host = cfg.redis["host"]
    redis_port = cfg.redis["port"]

    log.info(f"Connecting to Redis at {redis_host}:{redis_port}...")
    try:
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        r.ping()
        log.info("Redis connection successful")
    except Exception as e:
        if redis_host != "localhost":
            log.warning(f"Failed to connect to {redis_host}: {e}")
            log.info("Retrying with localhost (local development mode)...")
            try:
                r = redis.Redis(
                    host="localhost", port=redis_port, decode_responses=True
                )
                r.ping()
                log.info("Redis connection successful on localhost")
            except Exception as e2:
                log.error(f"Failed to connect to Redis on localhost: {e2}")
                return 1
        else:
            log.error(f"Failed to connect to Redis: {e}")
            return 1

    # Clear stream if requested
    if args.clear and not args.dry_run:
        log.info(f"Clearing Redis stream '{args.stream}'...")
        try:
            r.delete(args.stream)
            log.info("Stream cleared")
        except Exception as e:
            log.error(f"Error clearing stream: {e}")

    # Initialize Telegram client
    log.info("Initializing Telegram client...")

    # IMPORTANT: This script cannot run while sentinel is active because both
    # would try to access the same Telethon session file (SQLite database).
    # SQLite does not support concurrent writers well, especially with WAL mode.
    #
    # To use this script:
    # 1. Stop sentinel: docker compose stop sentinel
    # 2. Run this script: docker compose run --rm sentinel python tools/simulate_populate_history.py ...
    # 3. Restart sentinel: docker compose start sentinel

    session_path = cfg.telegram_session
    log.info(f"Using Telegram session: '{session_path}'")

    if not os.path.exists(session_path):
        log.error(f"Session file not found at {session_path}")
        log.error(
            "Please ensure you have authenticated first by running the main application."
        )
        return 1

    # Create client with session file
    # Note: sentinel must be stopped first to avoid "database is locked" errors
    client = TelegramClient(
        session_path,
        int(api_id),
        api_hash,
        system_version="TGSentinel History Populator",
    )

    try:
        await client.connect()

        if not await client.is_user_authorized():
            log.error(
                "Client not authorized. Please run the main application first to authenticate."
            )
            return 1

        log.info("Telegram client connected and authorized")

        # Determine which channels to fetch from
        if args.channel_id:
            channel_ids = [args.channel_id]
            log.info(f"Fetching from single channel: {args.channel_id}")
        else:
            # Only fetch from channels (negative IDs), not private users (positive IDs)
            channel_ids = [ch.id for ch in cfg.channels if ch.id < 0]
            monitored_user_ids = [ch.id for ch in cfg.channels if ch.id > 0]

            log.info(f"Fetching from {len(channel_ids)} configured channels")
            if monitored_user_ids:
                log.warning(
                    f"Skipping {len(monitored_user_ids)} monitored users (private chats). "
                    "Use --channel-id with a specific user ID to fetch from private chats, "
                    "but note this only works if you have an active dialog with that user."
                )

        if not channel_ids:
            log.error("No channels configured. Add channels to config/tgsentinel.yml")
            return 1

        # Build a map of channel IDs to entities first
        log.info("Resolving all channel entities...")
        id_to_entity = {}
        async for dialog in client.iter_dialogs():
            if dialog.entity.id in channel_ids:
                id_to_entity[dialog.entity.id] = dialog.entity
                # Get name safely - Users have first_name, Channels/Chats have title
                entity_name = (
                    getattr(dialog.entity, "title", None)
                    or getattr(dialog.entity, "first_name", None)
                    or f"Entity {dialog.entity.id}"
                )
                log.debug(f"Resolved entity for {dialog.entity.id}: {entity_name}")

        log.info(f"Resolved {len(id_to_entity)} out of {len(channel_ids)} channels")

        # Fetch messages from all channels
        all_messages = []
        for channel_id in channel_ids:
            if channel_id not in id_to_entity:
                log.warning(f"Skipping channel {channel_id}: not found in dialogs")
                continue
            messages = await fetch_channel_history(
                client, id_to_entity[channel_id], args.limit
            )
            all_messages.extend(messages)

        log.info(f"Total messages fetched: {len(all_messages)}")

        if args.dry_run:
            log.info("Dry run mode - not adding to Redis")
            log.info(
                f"Would have added {len(all_messages)} messages to stream '{args.stream}'"
            )

            # Show sample of first few messages
            if all_messages:
                log.info("\nSample messages:")
                for i, msg in enumerate(all_messages[:3], 1):
                    log.info(f"\n  Message {i}:")
                    log.info(f"    Channel: {msg['chat_title']}")
                    log.info(f"    Sender: {msg['sender_name']}")
                    log.info(f"    Text: {msg['text'][:100]}...")
                    log.info(f"    Timestamp: {msg['timestamp']}")
        else:
            # Add messages to Redis
            added = populate_redis_stream(r, all_messages, args.stream)

            # Show stream info
            stream_len = r.xlen(args.stream)
            log.info("\nRedis stream info:")
            log.info(f"  Stream: {args.stream}")
            log.info(f"  Total entries: {stream_len}")
            log.info(f"  Added this run: {added}")

        log.info("\n✅ History population complete!")

    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            log.error("=" * 60)
            log.error("DATABASE IS LOCKED!")
            log.error("The Telegram session file is being used by another process.")
            log.error("")
            log.error("To fix this, stop the sentinel service first:")
            log.error("  docker compose stop sentinel")
            log.error("")
            log.error("Then run this script again:")
            log.error(
                "  docker compose run --rm sentinel python tools/simulate_populate_history.py ..."
            )
            log.error("=" * 60)
            return 1
        else:
            raise
    except Exception as e:
        log.error(f"Error during execution: {e}", exc_info=True)
        return 1

    finally:
        if client.is_connected():
            await client.disconnect()
            log.info("Telegram client disconnected")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
