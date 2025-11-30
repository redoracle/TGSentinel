#!/usr/bin/env python3
"""
Send test messages to a specific Telegram channel.

This is a specialized version of simulate_live_feed.py that sends messages
to a channel instead of between two accounts. Use this for testing TG Sentinel
with controlled test messages.

Usage:
  python tools/simulate_channel_feed.py \
      --session ./my_italian.session \
      --api-id 29548417 \
      --api-hash ac4afdbd4805f491f55f7d836a880c92 \
      --channel "Redoracle Security" \
      --messages-file ./Test_alerts.txt \
      --interval 2
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import List

from telethon import TelegramClient
from telethon.tl.types import Channel, User


def _resolve_api_credentials(args) -> tuple[int, str]:
    api_id = args.api_id or os.getenv("TG_API_ID")
    api_hash = args.api_hash or os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        print(
            "Error: API credentials required. Provide --api-id/--api-hash "
            "or set TG_API_ID and TG_API_HASH."
        )
        sys.exit(1)

    try:
        api_id_int = int(api_id)
    except ValueError:
        print("Error: API ID must be an integer")
        sys.exit(1)

    return api_id_int, api_hash


def _session_base(path: str) -> str:
    """Strip .session extension as Telethon adds it automatically."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print(f"Error: Session file does not exist: {p}")
        sys.exit(1)
    return str(p.with_suffix(""))


async def _init_client(session_path: str, api_id: int, api_hash: str) -> TelegramClient:
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print(
            f"Error: Session at {session_path}.session is not authorized. "
            "Use tools/generate_session.py to create a valid session."
        )
        client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    if not isinstance(me, User):
        print("Error: Could not fetch account info for session:", session_path)
        client.disconnect()
        sys.exit(1)

    username = f"@{me.username}" if me.username else None
    display = username or f"{me.first_name or ''} {me.last_name or ''}".strip()
    print(f"Connected as {display} (id={me.id}) using {session_path}.session")
    return client


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Send test messages to a Telegram channel"
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Path to Telethon session file (e.g., ./my_italian.session)",
    )
    parser.add_argument("--api-id", help="Telegram API ID (or TG_API_ID env)")
    parser.add_argument("--api-hash", help="Telegram API hash (or TG_API_HASH env)")
    parser.add_argument(
        "--channel",
        required=True,
        help='Channel username (with @) or exact title (e.g., "Redoracle Security")',
    )
    parser.add_argument(
        "--messages-file",
        required=True,
        type=str,
        help="Path to text file with one message per line",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between messages (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show messages without sending",
    )

    args = parser.parse_args()
    api_id, api_hash = _resolve_api_credentials(args)

    # Load messages from file
    msg_path = Path(args.messages_file).expanduser().resolve()
    if not msg_path.exists():
        print(f"Error: messages file not found: {msg_path}")
        return 1

    messages: List[str] = []
    with msg_path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if text:
                messages.append(text)

    if not messages:
        print(f"Error: messages file {msg_path} is empty")
        return 1

    print(f"Loaded {len(messages)} messages from {msg_path}")

    if args.dry_run:
        print("\n=== DRY RUN MODE ===")
        for i, msg in enumerate(messages, 1):
            print(f"[{i}] {msg[:100]}{'...' if len(msg) > 100 else ''}")
        return 0

    session_path = _session_base(args.session)
    client = await _init_client(session_path, api_id, api_hash)

    try:
        # Resolve channel entity
        print(f"Resolving channel: {args.channel}")
        try:
            channel = await client.get_entity(args.channel)
        except Exception as e:
            print(f"Error: Could not find channel '{args.channel}': {e}")
            print("\nTrying to search in dialogs...")

            # Search through dialogs for matching channel
            async for dialog in client.iter_dialogs(limit=100):
                if isinstance(dialog.entity, Channel):
                    if dialog.title == args.channel or (
                        hasattr(dialog.entity, "username")
                        and dialog.entity.username == args.channel.lstrip("@")
                    ):
                        channel = dialog.entity
                        print(f"Found channel: {dialog.title} (ID: {channel.id})")
                        break
            else:
                print(f"Error: Channel '{args.channel}' not found in your dialogs")
                print("\nMake sure you:")
                print("1. Have permission to post to the channel")
                print("2. The channel name/username is correct")
                print("3. You've interacted with the channel before")
                return 1

        if not isinstance(channel, Channel):
            print(f"Error: '{args.channel}' is not a channel")
            return 1

        # Check if we can send messages
        print(f"Preparing to send {len(messages)} messages to: {channel.title}")
        print(f"Channel ID: {channel.id}")
        print(f"Interval: {args.interval}s between messages\n")

        # Send messages
        for i, msg in enumerate(messages, 1):
            try:
                await client.send_message(channel, msg)
                preview = msg[:80] + "..." if len(msg) > 80 else msg
                print(f"[{i}/{len(messages)}] ✓ Sent: {preview}")

                if i < len(messages):
                    await asyncio.sleep(args.interval)

            except Exception as send_err:
                print(f"[{i}/{len(messages)}] ✗ Error: {send_err}")
                print(f"  Message: {msg[:100]}...")

        print(f"\n✓ Done! Sent {len(messages)} messages to {channel.title}")
        return 0

    except KeyboardInterrupt:
        print("\n✗ Interrupted by user")
        return 1
    finally:
        client.disconnect()


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
