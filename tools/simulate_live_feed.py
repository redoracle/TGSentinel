#!/usr/bin/env python3
"""
Simulate a live Telegram feed between two accounts.

Given two Telethon session files (created via tools/generate_session.py)
for two different Telegram accounts, this script will make one or both
accounts send messages at a fixed interval. This is useful for testing
TG Sentinel's ingestion using real Telegram traffic, e.g. simulating a
one‑way "professional helper" account sending messages to a user.

Usage (from repo root):

  python tools/simulate_live_feed.py \
      --session-a ./my_dutch.session \
      --session-b ./temp_test.session \
      --api-id 123456 \
      --api-hash abcdef0123456789abcdef0123456789 \
      --direction a-to-b \
      --interval 5 \
      --count 20

Using a text file of messages (one per line):

  python tools/simulate_live_feed.py \
      --session-a ./helper.session \
      --session-b ./client.session \
      --messages-file ./messages.txt \
      --direction a-to-b \
      --interval 10

API credentials:
  - You can pass --api-id / --api-hash on the command line
    or set TG_API_ID / TG_API_HASH in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from telethon import TelegramClient
from telethon.tl.types import User


def _resolve_api_credentials(args) -> Tuple[int, str]:
    api_id = args.api_id or os.getenv("TG_API_ID")
    api_hash = args.api_hash or os.getenv("TG_API_HASH")

    if not api_id or not api_hash:
        print(
            "Error: API credentials are required. Provide --api-id/--api-hash "
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
    """
    Telethon appends '.session' to the provided name. The session files
    created by tools/generate_session.py already have this extension, so
    we strip it here to reuse those files instead of creating new ones.
    """
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
        client.disconnect()  # disconnect() is synchronous
        sys.exit(1)

    me = await client.get_me()
    if not isinstance(me, User):
        print("Error: Could not fetch account info for session:", session_path)
        client.disconnect()  # disconnect() is synchronous
        sys.exit(1)

    username = f"@{me.username}" if me.username else None
    display = username or f"{me.first_name or ''} {me.last_name or ''}".strip()
    print(f"Connected as {display} (id={me.id}) using session {session_path}.session")
    return client


def _build_message(prefix: str, seq: int) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    return f"[{prefix}] message #{seq} at {now}"


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate live Telegram traffic between two accounts"
    )
    parser.add_argument(
        "--session-a",
        required=True,
        help="Path to Telethon session file for account A (e.g., ./my_dutch.session)",
    )
    parser.add_argument(
        "--session-b",
        required=True,
        help="Path to Telethon session file for account B (e.g., ./temp_test.session)",
    )
    parser.add_argument("--api-id", help="Telegram API ID (or TG_API_ID env)")
    parser.add_argument("--api-hash", help="Telegram API hash (or TG_API_HASH env)")
    parser.add_argument(
        "--direction",
        choices=["a-to-b", "b-to-a", "both"],
        default="a-to-b",
        help="Send direction: A→B, B→A, or both (default: a-to-b)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between message batches (default: 5.0)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="Total message batches to send (default: 20)",
    )
    parser.add_argument(
        "--prefix-a",
        default="A->B",
        help="Message prefix when A sends (default: 'A->B')",
    )
    parser.add_argument(
        "--prefix-b",
        default="B->A",
        help="Message prefix when B sends (default: 'B->A')",
    )
    parser.add_argument(
        "--messages-file",
        type=str,
        help=(
            "Path to a text file with one message per line. "
            "Messages are sent in order, skipping blank lines."
        ),
    )

    args = parser.parse_args()

    api_id, api_hash = _resolve_api_credentials(args)

    # Load optional message script from file
    messages: List[str] = []
    if args.messages_file:
        msg_path = Path(args.messages_file).expanduser().resolve()
        if not msg_path.exists():
            print(f"Error: messages file not found: {msg_path}")
            return 1
        with msg_path.open("r", encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if text:
                    messages.append(text)
        if not messages:
            print(f"Error: messages file {msg_path} is empty")
            return 1

    session_a = _session_base(args.session_a)
    session_b = _session_base(args.session_b)

    client_a = await _init_client(session_a, api_id, api_hash)
    client_b = await _init_client(session_b, api_id, api_hash)

    try:
        me_a = await client_a.get_me()
        me_b = await client_b.get_me()

        # Type assertions to help Pylance understand these are User objects
        if not isinstance(me_a, User) or not isinstance(me_b, User):
            print("Error: Could not fetch user information")
            return 1

        # Use username if available, fallback to user ID
        # For fresh sessions, username is more reliable than user ID
        target_a_to_b = me_b.username if me_b.username else me_b.id
        target_b_to_a = me_a.username if me_a.username else me_a.id

        # Pre-fetch entities to populate the session cache
        # This is critical for fresh sessions that don't have the entities cached yet
        try:
            if args.direction in ("a-to-b", "both"):
                await client_a.get_entity(target_a_to_b)
            if args.direction in ("b-to-a", "both"):
                await client_b.get_entity(target_b_to_a)
        except Exception as entity_err:
            print(f"Warning: Could not pre-fetch entities: {entity_err}")
            # Try fetching dialogs to populate entity cache
            try:
                print("Attempting to fetch dialogs to populate entity cache...")
                await client_a.get_dialogs(limit=20)
                await client_b.get_dialogs(limit=20)
                # Retry entity fetch
                if args.direction in ("a-to-b", "both"):
                    await client_a.get_entity(target_a_to_b)
                if args.direction in ("b-to-a", "both"):
                    await client_b.get_entity(target_b_to_a)
                print("Entity cache populated successfully after fetching dialogs")
            except Exception as dialog_err:
                print(f"Error: Could not populate entity cache: {dialog_err}")
                print("\nTo fix this:")
                print(
                    "1. Ensure the two accounts have sent at least one message to each other before"
                )
                print("2. Or use accounts that are in each other's contacts")
                return 1

        total_batches = args.count
        if messages:
            # For scripted conversations, cap by number of messages
            total_batches = min(args.count, len(messages))

        print(
            f"Starting live feed simulation: direction={args.direction}, "
            f"interval={args.interval}s, count={total_batches}"
        )
        print("Press Ctrl+C to stop early.\n")

        for i in range(1, total_batches + 1):
            if args.direction in ("a-to-b", "both"):
                if messages:
                    text = messages[i - 1]
                else:
                    text = _build_message(args.prefix_a, i)
                await client_a.send_message(target_a_to_b, text)
                print(f"[{i}] A -> B: {text}")

            if args.direction in ("b-to-a", "both"):
                if messages:
                    text = messages[i - 1]
                else:
                    text = _build_message(args.prefix_b, i)
                await client_b.send_message(target_b_to_a, text)
                print(f"[{i}] B -> A: {text}")

            if i < total_batches:
                await asyncio.sleep(args.interval)

        print("\nDone sending messages.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted by user, stopping simulation.")
        return 0
    finally:
        client_a.disconnect()  # disconnect() is synchronous
        client_b.disconnect()  # disconnect() is synchronous


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
